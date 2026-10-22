from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from slurmscope.db.models import RunStatus
from slurmscope.services import transition


def test_state_transition_records_event_and_timestamp():
    run = SimpleNamespace(
        id=uuid4(),
        status=RunStatus.RUNNING,
        updated_at=None,
        started_at=None,
        finished_at=None,
    )
    db = Mock()
    transition(db, run, RunStatus.SUCCEEDED, "exit code 0")
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None
    event = db.add.call_args.args[0]
    assert event.previous_status == RunStatus.RUNNING
    assert event.new_status == RunStatus.SUCCEEDED
    assert event.message == "exit code 0"


def test_repeated_state_poll_is_idempotent():
    run = SimpleNamespace(
        id=uuid4(),
        status=RunStatus.RUNNING,
        updated_at=None,
        started_at=None,
        finished_at=None,
    )
    db = Mock()
    transition(db, run, RunStatus.RUNNING)
    db.add.assert_not_called()
    assert run.updated_at is None


def test_terminal_run_cannot_return_to_running():
    run = SimpleNamespace(
        id=uuid4(),
        status=RunStatus.SUCCEEDED,
        updated_at=None,
        started_at=None,
        finished_at=None,
    )
    with pytest.raises(ValueError, match="SUCCEEDED -> RUNNING"):
        transition(Mock(), run, RunStatus.RUNNING)
