import pytest

from slurmscope.executors.base import RunSpec
from slurmscope.executors.slurm import SlurmError, SlurmExecutor


@pytest.mark.parametrize(
    "state,expected",
    [
        ("PENDING", "PENDING"),
        ("CONFIGURING", "PENDING"),
        ("RUNNING", "RUNNING"),
        ("COMPLETING", "RUNNING"),
        ("COMPLETED", "SUCCEEDED"),
        ("CANCELLED by 12", "CANCELLED"),
        ("TIMEOUT", "FAILED"),
        ("OUT_OF_MEMORY", "FAILED"),
        ("NODE_FAIL", "FAILED"),
        ("FUTURE_STATE", "UNKNOWN"),
    ],
)
def test_slurm_states_normalize(state, expected):
    assert SlurmExecutor._normalize(state) == expected


def test_sbatch_job_id_with_cluster_suffix(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    calls = []
    monkeypatch.setattr(executor, "_run", lambda argv: calls.append(argv) or "12345;cluster-a")
    run = RunSpec("abcdef123456", tmp_path, {"seed": 2}, {"cpus_per_task": 2})
    assert executor.submit(run) == "12345"
    script = (tmp_path / "submit.sh").read_text()
    assert script.index("#SBATCH") < script.index("set -eu")
    assert "--cpus-per-task=2" in script


def test_sbatch_job_id_without_cluster_suffix(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    monkeypatch.setattr(executor, "_run", lambda argv: "9876")
    assert executor.submit(RunSpec("12345678", tmp_path, {}, {})) == "9876"


def test_malformed_sbatch_output_is_rejected(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    monkeypatch.setattr(executor, "_run", lambda argv: "job-123")
    with pytest.raises(SlurmError, match="malformed"):
        executor.submit(RunSpec("12345678", tmp_path, {}, {}))


def test_squeue_status_is_parsed(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    monkeypatch.setattr(executor, "_run", lambda argv: "123|RUNNING|node-a")
    assert executor.status(RunSpec("a", tmp_path, {}, {}), "123").state == "RUNNING"


def test_sacct_fallback_resolves_terminal_state(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    monkeypatch.setattr(
        executor,
        "_run",
        lambda argv: "" if argv[0] == "squeue" else "123 COMPLETED 0:0",
    )
    state = executor.status(RunSpec("a", tmp_path, {}, {}), "123")
    assert (state.state, state.exit_code) == ("SUCCEEDED", 0)


def test_absence_from_queue_and_accounting_is_unknown(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    monkeypatch.setattr(executor, "_run", lambda argv: "")
    assert executor.status(RunSpec("a", tmp_path, {}, {}), "123").state == "UNKNOWN"


def test_accounting_outage_is_unknown(monkeypatch, tmp_path):
    executor = SlurmExecutor()

    def command(argv):
        if argv[0] == "squeue":
            return ""
        raise SlurmError("offline")

    monkeypatch.setattr(executor, "_run", command)
    assert executor.status(RunSpec("a", tmp_path, {}, {}), "123").state == "UNKNOWN"


def test_scheduler_outage_uses_bounded_retry_backoff(monkeypatch, tmp_path):
    executor = SlurmExecutor()
    calls = []

    def command(argv):
        calls.append(argv)
        raise SlurmError("offline")

    monkeypatch.setattr(executor, "_run", command)
    run = RunSpec("a", tmp_path, {}, {})
    assert executor.status(run, "123").state == "UNKNOWN"
    assert executor.status(run, "123").message == "Slurm polling is waiting for the retry backoff"
    assert len(calls) == 1


def test_slurm_cancel_refuses_untracked_shape(monkeypatch, tmp_path):
    with pytest.raises(SlurmError, match="malformed"):
        SlurmExecutor().cancel(RunSpec("a", tmp_path, {}, {}), "123;cluster")


def test_slurm_queries_and_cancellation_are_scoped_to_worker_user(monkeypatch, tmp_path):
    executor = SlurmExecutor(user="researcher")
    calls = []

    def command(argv):
        calls.append(argv)
        return "123|RUNNING|node-a" if argv[0] == "squeue" else ""

    monkeypatch.setattr(executor, "_run", command)
    run = RunSpec("a", tmp_path, {}, {})
    executor.status(run, "123")
    executor.cancel(run, "123")
    assert ["-u", "researcher"] == calls[0][2:4]
    assert ["scancel", "-u", "researcher", "123"] == calls[1]


def test_batch_status_queries_active_and_accounting_jobs_once(monkeypatch, tmp_path):
    executor = SlurmExecutor(user="researcher")
    calls = []

    def command(argv):
        calls.append(argv)
        if argv[0] == "squeue":
            return "101|RUNNING|node-a"
        return "102 COMPLETED 0:0"

    monkeypatch.setattr(executor, "_run", command)
    jobs = [(RunSpec("a", tmp_path, {}, {}), "101"), (RunSpec("b", tmp_path, {}, {}), "102")]
    states = executor.status_many(jobs)
    assert states["101"].state == "RUNNING"
    assert states["102"].state == "SUCCEEDED"
    assert len(calls) == 2
    assert calls[0][calls[0].index("-j") + 1] == "101,102"
    assert calls[1][calls[1].index("-j") + 1] == "102"
