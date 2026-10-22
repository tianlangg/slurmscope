from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    artifact_path: Path
    parameters: dict[str, Any]
    resources: dict[str, Any]


@dataclass(frozen=True)
class JobSnapshot:
    state: str
    exit_code: int | None = None
    message: str | None = None


class Executor(Protocol):
    def submit(self, run: RunSpec) -> str: ...
    def status(self, run: RunSpec, external_id: str) -> JobSnapshot: ...
    def cancel(self, run: RunSpec, external_id: str) -> None: ...
    def logs(self, run: RunSpec, stream: str, tail: int) -> str: ...
