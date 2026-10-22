import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from slurmscope.db.database import Base

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Backend(enum.StrEnum):
    local = "local"
    slurm = "slurm"


class RunStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    LOST = "LOST"


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    backend: Mapped[Backend] = mapped_column(Enum(Backend, native_enum=False), nullable=False)
    sweep_spec: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    resource_spec: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class LaunchRequest(Base):
    __tablename__ = "launch_requests"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE")
    )
    run_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_experiment_status", "experiment_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    backend: Mapped[Backend] = mapped_column(Enum(Backend, native_enum=False), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), default=RunStatus.QUEUED, index=True
    )
    external_job_id: Mapped[str | None] = mapped_column(String(160))
    cancel_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submission_token: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), default=uuid.uuid4, unique=True
    )
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    experiment: Mapped[Experiment] = relationship(back_populates="runs")
    metrics: Mapped[list["RunMetric"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunMetric(Base):
    __tablename__ = "run_metrics"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_metric_run_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run: Mapped[Run] = relationship(back_populates="metrics")


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    previous_status: Mapped[RunStatus | None] = mapped_column(Enum(RunStatus, native_enum=False))
    new_status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), nullable=False
    )
    message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
