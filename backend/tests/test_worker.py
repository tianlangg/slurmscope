import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from slurmscope.db.models import Backend, Experiment, Run, RunEvent, RunStatus
from slurmscope.executors.local import LocalExecutor
from slurmscope.executors.slurm import SlurmExecutor
from slurmscope.worker.runner import reconcile_startup, tick


def test_worker_runs_local_job_and_persists_terminal_metrics(client):
    created = client.post(
        "/api/experiments",
        json={
            "name": "worker integration",
            "backend": "local",
            "sweep_spec": {"seed": [4], "steps": [1], "delay_seconds": [0]},
        },
    ).json()
    launched = client.post(
        f"/api/experiments/{created['id']}/launch",
        headers={"Idempotency-Key": "worker-integration-001"},
    ).json()

    for _ in range(50):
        tick(LocalExecutor(), SlurmExecutor())
        run = client.get(f"/api/runs/{launched['run_ids'][0]}").json()
        if run["status"] in {"SUCCEEDED", "FAILED", "LOST"}:
            break
        time.sleep(0.02)

    assert run["status"] == "SUCCEEDED"
    assert {metric["name"] for metric in run["metrics"]} == {
        "accuracy",
        "loss",
        "wall_time_seconds",
    }
    history = client.get(f"/api/runs/{run['id']}/events").json()
    assert [event["new_status"] for event in history][-2:] == ["RUNNING", "SUCCEEDED"]


def test_worker_confirms_active_local_cancellation(client):
    created = client.post(
        "/api/experiments",
        json={
            "name": "cancel integration",
            "backend": "local",
            "sweep_spec": {"seed": [1], "steps": [100], "delay_seconds": [0.2]},
        },
    ).json()
    launched = client.post(
        f"/api/experiments/{created['id']}/launch",
        headers={"Idempotency-Key": "worker-cancel-001"},
    ).json()
    run_id = launched["run_ids"][0]
    local = LocalExecutor()
    slurm = SlurmExecutor()
    tick(local, slurm)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "RUNNING"
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "CANCELLING"

    for _ in range(50):
        tick(local, slurm)
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] == "CANCELLED":
            break
        time.sleep(0.02)

    assert run["status"] == "CANCELLED"


def test_restart_marks_unresolved_slurm_submission_unknown(client, db_session: Session):
    experiment = Experiment(
        name="ambiguous submit",
        template_key="synthetic_benchmark",
        backend=Backend.slurm,
        sweep_spec={"seed": [1]},
        resource_spec={},
    )
    db_session.add(experiment)
    db_session.flush()
    run = Run(
        experiment_id=experiment.id,
        parameters={"seed": 1},
        backend=Backend.slurm,
        status=RunStatus.SUBMITTING,
        artifact_path=str(Path("/tmp") / str(uuid4())),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        RunEvent(run_id=run.id, new_status=RunStatus.SUBMITTING, message="Persisted before sbatch")
    )
    db_session.commit()
    run_id = run.id

    reconcile_startup()
    db_session.refresh(run)
    assert run.id == run_id
    assert run.status == RunStatus.UNKNOWN
    assert "reconciliation" in db_session.scalar(
        select(RunEvent.message)
        .where(RunEvent.run_id == run.id)
        .order_by(RunEvent.created_at.desc())
    )
