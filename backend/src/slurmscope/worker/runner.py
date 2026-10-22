import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from slurmscope.core.config import settings
from slurmscope.db.database import SessionLocal
from slurmscope.db.models import Experiment, Run, RunMetric, RunStatus
from slurmscope.executors.base import RunSpec
from slurmscope.executors.local import LocalExecutor
from slurmscope.executors.slurm import SlurmExecutor
from slurmscope.services import transition

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("slurmscope.worker")
TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.LOST}


def spec_for(run: Run, experiment: Experiment) -> RunSpec:
    return RunSpec(str(run.id), Path(run.artifact_path), run.parameters, experiment.resource_spec)


def ingest_metrics(db: Session, run: Run) -> None:
    path = Path(run.artifact_path) / "metrics.json"
    if not path.is_file():
        return
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("metrics must be a JSON object")
        for name, value in values.items():
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 80
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                continue
            if value != value or abs(value) == float("inf"):
                continue
            current = db.scalar(
                select(RunMetric).where(RunMetric.run_id == run.id, RunMetric.name == name)
            )
            if current:
                current.value = float(value)
            else:
                db.add(RunMetric(run_id=run.id, name=name, value=float(value)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Cannot ingest metrics for run %s: %s", run.id, exc)


def reconcile_startup() -> None:
    with SessionLocal() as db:
        active = list(
            db.scalars(
                select(Run).where(
                    Run.status.in_(
                        [
                            RunStatus.SUBMITTING,
                            RunStatus.PENDING,
                            RunStatus.RUNNING,
                            RunStatus.CANCELLING,
                            RunStatus.UNKNOWN,
                        ]
                    )
                )
            )
        )
        for run in active:
            if run.backend.value == "slurm" and not run.external_job_id:
                transition(
                    db,
                    run,
                    RunStatus.UNKNOWN,
                    "Submission outcome requires operator reconciliation; "
                    "automatic resubmission is disabled",
                )
            elif run.backend.value == "local" and not run.external_job_id:
                transition(
                    db,
                    run,
                    RunStatus.LOST,
                    "Worker restarted before process identity was persisted",
                )
        db.commit()


def tick(local: LocalExecutor, slurm: SlurmExecutor) -> None:
    with SessionLocal() as db:
        # Reconcile stateful jobs first so restart does not blindly resubmit work.
        active = list(
            db.scalars(
                select(Run)
                .options(selectinload(Run.experiment), selectinload(Run.metrics))
                .where(
                    Run.status.in_(
                        [
                            RunStatus.SUBMITTING,
                            RunStatus.PENDING,
                            RunStatus.RUNNING,
                            RunStatus.CANCELLING,
                            RunStatus.UNKNOWN,
                        ]
                    )
                )
            )
        )
        slurm_jobs = [
            (spec_for(run, run.experiment), run.external_job_id)
            for run in active
            if run.backend.value == "slurm" and run.external_job_id
        ]
        slurm_snapshots = slurm.status_many(
            [(spec, external_id) for spec, external_id in slurm_jobs if external_id]
        )
        for run in active:
            if not run.external_job_id:
                if run.backend.value == "slurm":
                    transition(
                        db,
                        run,
                        RunStatus.UNKNOWN,
                        "No recorded Slurm job ID; reconcile submission manually",
                    )
                else:
                    transition(db, run, RunStatus.LOST, "No recorded local process identity")
                continue
            executor = local if run.backend.value == "local" else slurm
            spec = spec_for(run, run.experiment)
            try:
                snapshot = (
                    local.status(spec, run.external_job_id)
                    if run.backend.value == "local"
                    else slurm_snapshots[run.external_job_id]
                )
                if run.status == RunStatus.CANCELLING:
                    if not run.cancel_sent:
                        executor.cancel(spec, run.external_job_id)
                        run.cancel_sent = True
                    if run.backend.value == "local":
                        snapshot = local.status(spec, run.external_job_id)
                    if run.backend.value == "local" and snapshot.state == "LOST":
                        transition(db, run, RunStatus.CANCELLED, "Local process stopped")
                    elif snapshot.state in {"CANCELLED", "SUCCEEDED", "FAILED"}:
                        transition(db, run, RunStatus(snapshot.state), snapshot.message)
                        if snapshot.exit_code is not None:
                            run.exit_code = snapshot.exit_code
                        if snapshot.state == "SUCCEEDED":
                            ingest_metrics(db, run)
                    elif snapshot.state == "UNKNOWN":
                        run.error_message = snapshot.message
                    continue
                status = RunStatus(snapshot.state)
                if status != run.status:
                    transition(db, run, status, snapshot.message)
                if snapshot.exit_code is not None:
                    run.exit_code = snapshot.exit_code
                if status == RunStatus.SUCCEEDED or (
                    status in TERMINAL and (Path(run.artifact_path) / "metrics.json").exists()
                ):
                    ingest_metrics(db, run)
            except Exception as exc:  # Scheduler outages are visible and retried on the next tick.
                logger.exception("Polling run %s failed", run.id)
                if run.status == RunStatus.CANCELLING and run.backend.value == "local":
                    transition(db, run, RunStatus.LOST, str(exc)[:500])
                elif run.status == RunStatus.CANCELLING:
                    run.error_message = str(exc)[:500]
                elif run.status != RunStatus.UNKNOWN:
                    transition(db, run, RunStatus.UNKNOWN, str(exc)[:500])

        db.commit()
        active_local_count = sum(
            1
            for run in db.scalars(
                select(Run).where(
                    Run.backend == "local",
                    Run.status.in_([RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCELLING]),
                )
            )
        )
        queued = list(
            db.scalars(
                select(Run)
                .options(selectinload(Run.experiment))
                .where(Run.status == RunStatus.QUEUED)
                .order_by(Run.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for run in queued:
            if run.backend.value == "local" and active_local_count >= settings.max_local_jobs:
                continue
            transition(db, run, RunStatus.SUBMITTING, "Worker claimed run")
            db.commit()  # Persist claim before external submission.
            try:
                executor = local if run.backend.value == "local" else slurm
                external_id = executor.submit(spec_for(run, run.experiment))
                run.external_job_id = external_id
                run.submitted_at = datetime.now(UTC)
                db.flush()
                db.refresh(run, attribute_names=["status"])
                if run.status == RunStatus.SUBMITTING:
                    transition(
                        db,
                        run,
                        RunStatus.PENDING if run.backend.value == "slurm" else RunStatus.RUNNING,
                        "Submitted to executor",
                    )
                db.commit()
                if run.backend.value == "local":
                    active_local_count += 1
            except Exception as exc:
                transition(db, run, RunStatus.FAILED, f"Submission failed: {exc}"[:500])
                run.error_message = str(exc)[:500]
                db.commit()


def main() -> None:
    Path(settings.artifact_root).mkdir(parents=True, exist_ok=True)
    reconcile_startup()
    local, slurm = LocalExecutor(), SlurmExecutor()
    logger.info("Worker started; poll interval %.1fs", settings.worker_poll_seconds)
    while True:
        try:
            tick(local, slurm)
        except Exception:
            logger.exception("Worker tick failed")
        time.sleep(max(1, settings.worker_poll_seconds))


if __name__ == "__main__":
    main()
