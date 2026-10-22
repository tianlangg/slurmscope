import getpass
import json
import subprocess
import time

from slurmscope.core.config import settings
from slurmscope.executors.base import JobSnapshot, RunSpec


class SlurmError(RuntimeError):
    pass


class SlurmExecutor:
    """CLI adapter. The worker host must have authorized Slurm CLI access and shared artifacts."""

    def __init__(self, timeout: int = 15, user: str | None = None):
        self.timeout = timeout
        self.user = user or getpass.getuser()
        self._failure_count = 0
        self._retry_after = 0.0

    def _record_failure(self) -> None:
        self._failure_count = min(self._failure_count + 1, 5)
        self._retry_after = time.monotonic() + min(5 * (2**self._failure_count), 120)

    def _run(self, argv: list[str]) -> str:
        try:
            result = subprocess.run(
                argv, check=True, capture_output=True, text=True, timeout=self.timeout, shell=False
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise SlurmError(f"Slurm command failed: {argv[0]}") from exc
        return result.stdout.strip()

    def submit(self, run: RunSpec) -> str:
        run.artifact_path.mkdir(parents=True, exist_ok=True)
        args_path = run.artifact_path / "parameters.json"
        args_path.write_text(json.dumps(run.parameters), encoding="utf-8")
        script = ["#!/bin/sh"]
        for key, option in (
            ("cpus_per_task", "cpus-per-task"),
            ("memory_mb", "mem"),
            ("time_limit_minutes", "time"),
            ("partition", "partition"),
            ("account", "account"),
        ):
            if key in run.resources:
                value = run.resources[key]
                script.append(f"#SBATCH --{option}={value}")
        if settings.slurm_account and "account" not in run.resources:
            script.append(f"#SBATCH --account={settings.slurm_account}")
        script.extend(
            [
                f"#SBATCH --job-name=slurmscope-{run.run_id[:8]}",
                f"#SBATCH --output={self._quote(str(run.artifact_path / 'stdout.log'))}",
                f"#SBATCH --error={self._quote(str(run.artifact_path / 'stderr.log'))}",
                "set -eu",
                f"cd {self._quote(str(run.artifact_path))}",
                f"python -m slurmscope.templates.synthetic_benchmark {self._quote(str(args_path))}",
            ]
        )
        script_path = run.artifact_path / "submit.sh"
        script_path.write_text("\n".join(script) + "\n", encoding="utf-8")
        output = self._run(["sbatch", "--parsable", str(script_path)])
        job_id = output.split(";", 1)[0]
        if not job_id.isdigit():
            raise SlurmError("sbatch returned a malformed job ID")
        return job_id

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"

    def status(self, run: RunSpec, external_id: str) -> JobSnapshot:
        return self.status_many([(run, external_id)])[external_id]

    def status_many(self, jobs: list[tuple[RunSpec, str]]) -> dict[str, JobSnapshot]:
        """Poll related jobs with one queue request and one accounting fallback."""
        if not jobs:
            return {}
        job_ids = [external_id for _, external_id in jobs]
        unknown = {
            job_id: JobSnapshot("UNKNOWN", None, "Job status is not available")
            for job_id in job_ids
        }
        if time.monotonic() < self._retry_after:
            return {
                job_id: JobSnapshot(
                    "UNKNOWN", None, "Slurm polling is waiting for the retry backoff"
                )
                for job_id in job_ids
            }
        try:
            active = self._run(
                ["squeue", "-h", "-u", self.user, "-j", ",".join(job_ids), "-o", "%i|%T|%R"]
            )
        except SlurmError:
            self._record_failure()
            return {
                job_id: JobSnapshot("UNKNOWN", None, "Slurm queue is temporarily unavailable")
                for job_id in job_ids
            }

        for line in active.splitlines():
            fields = line.split("|", 2)
            if len(fields) != 3 or fields[0] not in unknown:
                continue
            unknown[fields[0]] = JobSnapshot(self._normalize(fields[1]), None, fields[2])

        missing = [job_id for job_id in job_ids if unknown[job_id].state == "UNKNOWN"]
        if not missing:
            self._failure_count = 0
            self._retry_after = 0.0
            return unknown
        try:
            argv = ["sacct", "-n", "-X", "-u", self.user]
            if settings.slurm_account:
                argv.extend(["-A", settings.slurm_account])
            argv.extend(["-j", ",".join(missing), "-o", "JobID,State,ExitCode"])
            accounting = self._run(argv)
        except SlurmError:
            self._record_failure()
            return {
                job_id: (
                    unknown[job_id]
                    if unknown[job_id].state != "UNKNOWN"
                    else JobSnapshot("UNKNOWN", None, "Slurm accounting is temporarily unavailable")
                )
                for job_id in job_ids
            }
        for line in accounting.splitlines():
            fields = line.split(None, 2)
            if len(fields) < 3:
                continue
            job_id, raw_state, raw_exit = fields
            if job_id not in missing:
                continue
            try:
                code = int(raw_exit.split(":", 1)[0])
            except ValueError:
                code = None
            unknown[job_id] = JobSnapshot(self._normalize(raw_state), code, raw_state)
        self._failure_count = 0
        self._retry_after = 0.0
        for job_id in missing:
            if unknown[job_id].state == "UNKNOWN":
                unknown[job_id] = JobSnapshot(
                    "UNKNOWN", None, "Job is absent from queue and accounting"
                )
        return unknown

    @staticmethod
    def _normalize(state: str) -> str:
        state = state.upper().split("+", 1)[0].split()[0]
        mapping = {
            "PENDING": "PENDING",
            "CONFIGURING": "PENDING",
            "RUNNING": "RUNNING",
            "COMPLETING": "RUNNING",
            "COMPLETED": "SUCCEEDED",
            "CANCELLED": "CANCELLED",
            "FAILED": "FAILED",
            "TIMEOUT": "FAILED",
            "OUT_OF_MEMORY": "FAILED",
            "NODE_FAIL": "FAILED",
            "PREEMPTED": "FAILED",
            "BOOT_FAIL": "FAILED",
        }
        return mapping.get(state, "UNKNOWN")

    def cancel(self, run: RunSpec, external_id: str) -> None:
        if not external_id.isdigit():
            raise SlurmError("Refusing to cancel malformed job ID")
        self._run(["scancel", "-u", self.user, external_id])

    def logs(self, run: RunSpec, stream: str, tail: int) -> str:
        path = run.artifact_path / f"{stream}.log"
        return (
            "\n".join(path.read_text(errors="replace").splitlines()[-tail:])
            if path.exists()
            else ""
        )
