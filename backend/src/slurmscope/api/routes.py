import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from slurmscope.api.schemas import (
    ExperimentInput,
    ExperimentRead,
    ExperimentUpdate,
    PreviewInput,
    RunRead,
)
from slurmscope.core.config import settings
from slurmscope.db.database import SessionLocal
from slurmscope.db.models import (
    Backend,
    Experiment,
    LaunchRequest,
    Run,
    RunEvent,
    RunStatus,
)
from slurmscope.services import expand_sweep, get_run, read_log, transition, validate_resource

router = APIRouter(prefix="/api")


def db_session() -> Any:
    with SessionLocal() as db:
        yield db


DB = Annotated[Session, Depends(db_session)]


def as_run(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "experiment_id": run.experiment_id,
        "parameters": run.parameters,
        "backend": run.backend.value,
        "status": run.status.value,
        "external_job_id": run.external_job_id,
        "exit_code": run.exit_code,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "submitted_at": run.submitted_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "metrics": [{"name": m.name, "value": m.value} for m in run.metrics],
    }


def find_experiment(db: Session, experiment_id: uuid.UUID) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Experiment not found")
    return experiment


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: DB) -> dict[str, str]:
    from sqlalchemy import text

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc
    return {"status": "ready", "database": "ok"}


@router.get("/templates")
def templates() -> list[dict[str, Any]]:
    return [
        {
            "key": "synthetic_benchmark",
            "name": "Synthetic Benchmark",
            "description": "Deterministic CPU benchmark that writes metrics.json",
            "parameters": [
                {"name": "learning_rate", "type": "number", "default": 0.1},
                {"name": "seed", "type": "integer", "default": 1},
                {"name": "steps", "type": "integer", "default": 100},
                {"name": "delay_seconds", "type": "number", "default": 0.1},
            ],
        }
    ]


@router.get("/backends")
def backends() -> list[dict[str, Any]]:
    return [
        {"key": "local", "available": True, "description": "Local subprocess worker"},
        {"key": "slurm", "available": settings.slurm_enabled, "description": "Slurm CLI worker"},
    ]


@router.get("/experiments", response_model=list[ExperimentRead])
def list_experiments(
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = None,
) -> list[Experiment]:
    query = select(Experiment).order_by(Experiment.created_at.desc()).limit(limit).offset(offset)
    if search:
        query = query.where(Experiment.name.ilike(f"%{search}%"))
    return list(db.scalars(query))


@router.post("/experiments", response_model=ExperimentRead, status_code=201)
def create_experiment(body: ExperimentInput, db: DB) -> Experiment:
    if body.template_key != "synthetic_benchmark":
        raise HTTPException(422, "Unknown task template")
    backend = Backend(body.backend.value)
    validate_resource(backend, body.resource_spec)
    expand_sweep(body.sweep_spec)
    experiment = Experiment(
        name=body.name,
        description=body.description,
        template_key=body.template_key,
        backend=backend,
        sweep_spec=body.sweep_spec,
        resource_spec=body.resource_spec,
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


@router.get("/experiments/{experiment_id}", response_model=ExperimentRead)
def experiment_detail(experiment_id: uuid.UUID, db: DB) -> Experiment:
    return find_experiment(db, experiment_id)


@router.patch("/experiments/{experiment_id}", response_model=ExperimentRead)
def update_experiment(experiment_id: uuid.UUID, body: ExperimentUpdate, db: DB) -> Experiment:
    item = find_experiment(db, experiment_id)
    values = body.model_dump(exclude_unset=True)
    if "sweep_spec" in values and values["sweep_spec"] is not None:
        expand_sweep(values["sweep_spec"])
    if "resource_spec" in values and values["resource_spec"] is not None:
        validate_resource(item.backend, values["resource_spec"])
    for key, value in values.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/experiments/{experiment_id}", status_code=204)
def delete_experiment(experiment_id: uuid.UUID, db: DB) -> Response:
    item = find_experiment(db, experiment_id)
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(
            Run.experiment_id == item.id,
            Run.status.in_(
                [
                    RunStatus.QUEUED,
                    RunStatus.SUBMITTING,
                    RunStatus.PENDING,
                    RunStatus.RUNNING,
                    RunStatus.CANCELLING,
                    RunStatus.UNKNOWN,
                ]
            ),
        )
    )
    if active:
        raise HTTPException(409, "Cannot delete an experiment with active runs")
    artifact_root = Path(settings.artifact_root).resolve()
    artifact_directory = artifact_root / str(item.id)
    if artifact_directory.is_symlink() or not artifact_directory.resolve().is_relative_to(
        artifact_root
    ):
        raise HTTPException(400, "Invalid experiment artifact directory")
    db.delete(item)
    db.commit()
    if artifact_directory.exists():
        shutil.rmtree(artifact_directory)
    return Response(status_code=204)


@router.post("/experiments/{experiment_id}/preview")
def preview(experiment_id: uuid.UUID, body: PreviewInput, db: DB) -> dict[str, Any]:
    item = find_experiment(db, experiment_id)
    combos = expand_sweep(body.sweep_spec or item.sweep_spec)
    return {"count": len(combos), "runs": combos}


@router.post("/experiments/{experiment_id}/launch", status_code=202)
def launch(
    experiment_id: uuid.UUID,
    db: DB,
    idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
) -> dict[str, Any]:
    item = find_experiment(db, experiment_id)
    prior = db.get(LaunchRequest, idempotency_key)
    if prior:
        if prior.experiment_id != item.id:
            raise HTTPException(409, "Idempotency key already belongs to another experiment")
        return {
            "experiment_id": str(item.id),
            "run_ids": prior.run_ids,
            "count": len(prior.run_ids),
            "replayed": True,
        }
    combos = expand_sweep(item.sweep_spec)
    root = Path(settings.artifact_root).resolve()
    run_ids = []
    for parameters in combos:
        run = Run(
            experiment_id=item.id,
            parameters=parameters,
            backend=item.backend,
            status=RunStatus.QUEUED,
            artifact_path=str(root / str(item.id) / str(uuid.uuid4())),
        )
        db.add(run)
        db.flush()
        run_ids.append(str(run.id))
        db.add(RunEvent(run_id=run.id, new_status=RunStatus.QUEUED, message="Run queued"))
    db.add(LaunchRequest(key=idempotency_key, experiment_id=item.id, run_ids=run_ids))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        prior = db.get(LaunchRequest, idempotency_key)
        if prior is None:
            raise
        if prior.experiment_id != item.id:
            raise HTTPException(
                409, "Idempotency key already belongs to another experiment"
            ) from exc
        return {
            "experiment_id": str(item.id),
            "run_ids": prior.run_ids,
            "count": len(prior.run_ids),
            "replayed": True,
        }
    return {
        "experiment_id": str(item.id),
        "run_ids": run_ids,
        "count": len(run_ids),
        "replayed": False,
    }


@router.get("/experiments/{experiment_id}/runs", response_model=list[RunRead])
def experiment_runs(
    experiment_id: uuid.UUID,
    db: DB,
    status: RunStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    find_experiment(db, experiment_id)
    query = (
        select(Run)
        .options(selectinload(Run.metrics))
        .where(Run.experiment_id == experiment_id)
        .order_by(Run.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(Run.status == status)
    return [as_run(run) for run in db.scalars(query)]


@router.get("/experiments/{experiment_id}/comparison")
def comparison(experiment_id: uuid.UUID, db: DB) -> dict[str, Any]:
    find_experiment(db, experiment_id)
    runs = db.scalars(
        select(Run)
        .options(selectinload(Run.metrics))
        .where(Run.experiment_id == experiment_id)
        .order_by(Run.created_at)
    ).all()
    return {
        "runs": [as_run(run) for run in runs],
        "metrics": sorted({m.name for run in runs for m in run.metrics}),
    }


@router.get("/runs", response_model=list[RunRead])
def list_runs(
    db: DB,
    status: RunStatus | None = None,
    experiment_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    query = (
        select(Run)
        .options(selectinload(Run.metrics))
        .order_by(Run.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(Run.status == status)
    if experiment_id:
        query = query.where(Run.experiment_id == experiment_id)
    return [as_run(run) for run in db.scalars(query)]


@router.get("/runs/{run_id}", response_model=RunRead)
def run_detail(run_id: uuid.UUID, db: DB) -> dict[str, Any]:
    return as_run(get_run(db, run_id))


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: uuid.UUID, db: DB) -> dict[str, Any]:
    run = get_run(db, run_id)
    if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.LOST}:
        return {"run_id": str(run.id), "status": run.status.value, "accepted": False}
    if run.status == RunStatus.QUEUED:
        transition(db, run, RunStatus.CANCELLED, "Cancelled before submission")
    elif run.status != RunStatus.CANCELLING:
        transition(db, run, RunStatus.CANCELLING, "Cancellation requested")
    db.commit()
    return {"run_id": str(run.id), "status": run.status.value, "accepted": True}


@router.get("/runs/{run_id}/logs")
def logs(
    run_id: uuid.UUID,
    db: DB,
    stream: str = Query("stdout"),
    tail: int = Query(200, ge=1),
) -> dict[str, str]:
    run = get_run(db, run_id)
    return {
        "run_id": str(run.id),
        "stream": stream,
        "content": read_log(run.artifact_path, stream, tail),
    }


@router.get("/runs/{run_id}/metrics")
def metrics(run_id: uuid.UUID, db: DB) -> list[dict[str, str | float]]:
    run = get_run(db, run_id)
    return [{"name": m.name, "value": m.value} for m in run.metrics]


@router.get("/runs/{run_id}/events")
def events(run_id: uuid.UUID, db: DB) -> list[dict[str, Any]]:
    get_run(db, run_id)
    history = db.scalars(
        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at)
    ).all()
    return [
        {
            "id": e.id,
            "previous_status": e.previous_status.value if e.previous_status else None,
            "new_status": e.new_status.value,
            "message": e.message,
            "created_at": e.created_at,
        }
        for e in history
    ]
