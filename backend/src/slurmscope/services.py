import itertools
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from slurmscope.core.config import settings
from slurmscope.db.models import (
    Backend,
    Run,
    RunEvent,
    RunStatus,
)

TEMPLATE_PARAMETERS = {"learning_rate", "seed", "steps", "delay_seconds"}
ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.SUBMITTING, RunStatus.CANCELLED},
    RunStatus.SUBMITTING: {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.UNKNOWN,
        RunStatus.LOST,
        RunStatus.CANCELLING,
    },
    RunStatus.PENDING: {
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.UNKNOWN,
    },
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.UNKNOWN,
        RunStatus.LOST,
    },
    RunStatus.CANCELLING: {
        RunStatus.CANCELLED,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.UNKNOWN,
        RunStatus.LOST,
    },
    RunStatus.UNKNOWN: {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.LOST,
    },
}


def expand_sweep(
    sweep: dict[str, list[int | float]], limit: int | None = None
) -> list[dict[str, int | float]]:
    if not sweep or any(not values for values in sweep.values()):
        raise HTTPException(422, "Sweep requires non-empty parameter values")
    unknown = set(sweep) - TEMPLATE_PARAMETERS
    if unknown:
        raise HTTPException(422, f"Unsupported template parameters: {', '.join(sorted(unknown))}")
    ranges = {
        "learning_rate": (0, 10),
        "seed": (0, 2**31 - 1),
        "steps": (1, 100_000),
        "delay_seconds": (0, 60),
    }
    for key, choices in sweep.items():
        for item in choices:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise HTTPException(422, f"Parameter {key} only accepts numeric values")
            if not math.isfinite(float(item)):
                raise HTTPException(422, f"Parameter {key} must be finite")
            if key in {"seed", "steps"} and not isinstance(item, int):
                raise HTTPException(422, f"Parameter {key} only accepts integers")
            lower, upper = ranges[key]
            if not lower <= item <= upper:
                raise HTTPException(422, f"Parameter {key} must be between {lower} and {upper}")
    count = math.prod(len(values) for values in sweep.values())
    if count > (limit or settings.max_runs_per_launch):
        raise HTTPException(
            422, f"Sweep expands to {count} runs; limit is {limit or settings.max_runs_per_launch}"
        )
    keys = sorted(sweep)
    return [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(sweep[k] for k in keys))
    ]


def transition(db: Session, run: Run, target: RunStatus, message: str | None = None) -> None:
    if run.status == target:
        return
    previous = run.status
    if target not in ALLOWED_TRANSITIONS.get(previous, set()):
        raise ValueError(f"Invalid run transition: {previous.value} -> {target.value}")
    run.status = target
    run.updated_at = datetime.now(UTC)
    if target in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.LOST}:
        run.finished_at = run.updated_at
    if target == RunStatus.RUNNING and run.started_at is None:
        run.started_at = run.updated_at
    db.add(
        RunEvent(
            run_id=run.id,
            previous_status=previous,
            new_status=target,
            message=message[:500] if message else None,
        )
    )


def validate_resource(backend: Backend, resource: dict[str, Any]) -> dict[str, Any]:
    allowed = {"cpus_per_task", "memory_mb", "time_limit_minutes", "partition", "account"}
    if set(resource) - allowed:
        raise HTTPException(422, "Unsupported resource setting")
    if backend == Backend.local and resource:
        raise HTTPException(422, "Resource settings only apply to Slurm runs")
    for key, upper in (
        ("cpus_per_task", settings.slurm_max_cpus),
        ("memory_mb", settings.slurm_max_memory_mb),
        ("time_limit_minutes", settings.slurm_max_time_minutes),
    ):
        if key in resource and (
            not isinstance(resource[key], int)
            or isinstance(resource[key], bool)
            or not 1 <= resource[key] <= upper
        ):
            raise HTTPException(422, f"{key} must be between 1 and {upper}")
    partitions = [part for part in settings.slurm_partitions.split(",") if part]
    for key in ("partition", "account"):
        if key in resource and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(resource[key])):
            raise HTTPException(422, f"Invalid Slurm {key}")
    if resource.get("partition") and partitions and resource["partition"] not in partitions:
        raise HTTPException(422, "Partition is not in the configured allowlist")
    if (
        settings.slurm_account
        and resource.get("account")
        and resource["account"] != settings.slurm_account
    ):
        raise HTTPException(422, "Account must match the configured Slurm account")
    if resource.get("account") and not settings.slurm_account:
        raise HTTPException(422, "Account selection requires a configured Slurm account")
    if not settings.slurm_enabled and backend == Backend.slurm:
        raise HTTPException(422, "Slurm backend is disabled; configure an authorized Slurm worker")
    return resource


def get_run(db: Session, run_id: UUID) -> Run:
    run = db.scalar(select(Run).options(selectinload(Run.metrics)).where(Run.id == run_id))
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


def safe_log_path(artifact: str, stream: str) -> Path:
    if stream not in {"stdout", "stderr"}:
        raise HTTPException(422, "stream must be stdout or stderr")
    root = Path(settings.artifact_root).resolve()
    directory = Path(artifact).resolve()
    if not directory.is_relative_to(root):
        raise HTTPException(400, "Invalid artifact location")
    path = directory / f"{stream}.log"
    if path.is_symlink() or (path.exists() and not path.resolve().is_relative_to(root)):
        raise HTTPException(400, "Invalid artifact file")
    return path


def read_log(artifact: str, stream: str, tail: int) -> str:
    if not 1 <= tail <= settings.log_tail_max_lines:
        raise HTTPException(422, f"tail must be between 1 and {settings.log_tail_max_lines}")
    path = safe_log_path(artifact, stream)
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - settings.log_tail_max_bytes))
        content = handle.read(settings.log_tail_max_bytes).decode("utf-8", errors="replace")
    return "\n".join(content.splitlines()[-tail:])
