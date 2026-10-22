import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Thread
from typing import BinaryIO

from slurmscope.core.config import settings
from slurmscope.executors.base import JobSnapshot, RunSpec


class LocalExecutor:
    """Runs only the bundled Python template using an argv array and isolated process group."""

    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen[bytes]] = {}

    @staticmethod
    def _capture(pipe: BinaryIO, path: Path) -> None:
        remaining = settings.log_file_max_bytes
        with path.open("wb") as output:
            while block := pipe.read(64 * 1024):
                if remaining:
                    captured = block[:remaining]
                    output.write(captured)
                    remaining -= len(captured)
        pipe.close()

    def submit(self, run: RunSpec) -> str:
        run.artifact_path.mkdir(parents=True, exist_ok=True)
        args_path = run.artifact_path / "parameters.json"
        args_path.write_text(json.dumps(run.parameters), encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-m", "slurmscope.templates.synthetic_benchmark", str(args_path)],
            cwd=run.artifact_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "SLURMSCOPE_ARTIFACT_DIR": str(run.artifact_path)},
        )
        self._processes[process.pid] = process
        assert process.stdout is not None and process.stderr is not None
        for pipe, stream in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            Thread(
                target=self._capture,
                args=(pipe, run.artifact_path / f"{stream}.log"),
                daemon=True,
            ).start()
        return f"{process.pid}:{self._process_start(process.pid)}"

    @staticmethod
    def _process_info(pid: int) -> tuple[str, str]:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            process_fields = stat[stat.rfind(")") + 2 :].split()
            return process_fields[19], process_fields[0]
        except (OSError, IndexError):
            return "unknown", "unknown"

    @classmethod
    def _process_start(cls, pid: int) -> str:
        return cls._process_info(pid)[0]

    def status(self, run: RunSpec, external_id: str) -> JobSnapshot:
        marker = run.artifact_path / "exit.json"
        if marker.is_file():
            try:
                result = json.loads(marker.read_text(encoding="utf-8"))
                code = int(result["exit_code"])
                return JobSnapshot("SUCCEEDED" if code == 0 else "FAILED", code)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                return JobSnapshot("FAILED", None, f"Invalid exit marker: {exc}")
        try:
            pid_text, start = external_id.split(":", 1)
            pid = int(pid_text)
            process = self._processes.get(pid)
            if process is not None and process.poll() is not None:
                return JobSnapshot(
                    "LOST", process.returncode, "Local process exited without an exit marker"
                )
            os.kill(pid, 0)
            process_start, process_state = self._process_info(pid)
            if process_state == "Z":
                return JobSnapshot("LOST", None, "Local process exited without an exit marker")
            if start != "unknown" and process_start != start:
                return JobSnapshot("LOST", None, "Process identity changed after restart")
            return JobSnapshot("RUNNING")
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            return JobSnapshot(
                "LOST", None, "Local process is no longer available and has no exit marker"
            )

    def cancel(self, run: RunSpec, external_id: str) -> None:
        pid_text, start = external_id.split(":", 1)
        pid = int(pid_text)
        if start != "unknown" and self._process_start(pid) != start:
            raise RuntimeError("Refusing to signal a process with a different identity")
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Some sandboxed developer hosts disallow process-group signals.
            # The bundled template has no child process tree, so signal its
            # identity-checked PID as a safe fallback.
            os.kill(pid, signal.SIGTERM)

    def logs(self, run: RunSpec, stream: str, tail: int) -> str:
        path = run.artifact_path / f"{stream}.log"
        return (
            "\n".join(path.read_text(errors="replace").splitlines()[-tail:])
            if path.exists()
            else ""
        )
