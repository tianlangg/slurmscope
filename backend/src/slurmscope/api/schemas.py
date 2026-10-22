from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BackendName(StrEnum):
    local = "local"
    slurm = "slurm"


class ExperimentInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    template_key: str = "synthetic_benchmark"
    backend: BackendName = BackendName.local
    sweep_spec: dict[str, list[int | float]]
    resource_spec: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sweep_spec")
    @classmethod
    def validate_grid(cls, value: dict[str, list[int | float]]) -> dict[str, list[int | float]]:
        if not value:
            raise ValueError("sweep_spec must contain at least one parameter")
        for key, choices in value.items():
            if not key or len(key) > 80 or not choices or len(choices) > 50:
                raise ValueError("Each parameter needs a name and 1 to 50 values")
            if any(
                isinstance(item, bool) or not isinstance(item, (int, float)) for item in choices
            ):
                raise ValueError(f"Parameter {key} only accepts numeric values")
        return value


class ExperimentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    sweep_spec: dict[str, list[int | float]] | None = None
    resource_spec: dict[str, Any] | None = None


class PreviewInput(BaseModel):
    sweep_spec: dict[str, list[int | float]] | None = None


class ExperimentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None
    template_key: str
    backend: BackendName
    sweep_spec: dict[str, list[int | float]]
    resource_spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MetricRead(BaseModel):
    name: str
    value: float


class RunRead(BaseModel):
    id: UUID
    experiment_id: UUID
    parameters: dict[str, Any]
    backend: BackendName
    status: str
    external_job_id: str | None
    exit_code: int | None
    error_message: str | None
    created_at: datetime
    submitted_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    metrics: list[MetricRead] = []


class EventRead(BaseModel):
    id: UUID
    previous_status: str | None
    new_status: str
    message: str | None
    created_at: datetime
