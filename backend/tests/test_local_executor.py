import json
import time
from pathlib import Path

from slurmscope.executors.base import RunSpec
from slurmscope.executors.local import LocalExecutor


def test_local_task_completes_and_writes_deterministic_metrics(tmp_path, monkeypatch):
    executor = LocalExecutor()
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1] / "src"))
    run = RunSpec(
        "run1", tmp_path, {"learning_rate": 0.1, "seed": 2, "steps": 1, "delay_seconds": 0}, {}
    )
    job_id = executor.submit(run)
    for _ in range(100):
        status = executor.status(run, job_id)
        if status.state in {"SUCCEEDED", "FAILED", "LOST"}:
            break
        time.sleep(0.05)
    assert status.state == "SUCCEEDED"
    assert json.loads((tmp_path / "metrics.json").read_text())["accuracy"] > 0.9
    assert "progress" in (tmp_path / "stdout.log").read_text()


def test_local_failure_exit_marker_is_failed(tmp_path):
    run = RunSpec("run", tmp_path, {}, {})
    (tmp_path / "exit.json").write_text('{"exit_code": 9}')
    assert LocalExecutor().status(run, "100:unknown").state == "FAILED"


def test_invalid_exit_marker_is_reported_as_failure(tmp_path):
    run = RunSpec("run", tmp_path, {}, {})
    (tmp_path / "exit.json").write_text("not json")
    assert LocalExecutor().status(run, "100:unknown").state == "FAILED"
