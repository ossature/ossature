from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


class TaskStatus(Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL = "manual"


def _coerce_verify(value: Any) -> list[str]:
    """Normalize a `verify` field to a list of command strings.

    Accepts either a single shell-command string (legacy form) or a list
    of strings (preferred form). An empty string becomes an empty list so
    "no verify" is represented uniformly.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise TypeError(f"verify must be a string or a list of strings, got {type(value).__name__}")


class PlannerTask(BaseModel):
    kind: Literal["task"] = "task"
    title: str
    description: str
    outputs: list[str]
    depends_on: list[int]
    spec_refs: list[str]
    arch_refs: list[str]
    verify: list[str]
    context_files: list[str] = []

    @field_validator("verify", mode="before")
    @classmethod
    def _normalize_verify(cls, value: Any) -> list[str]:
        return _coerce_verify(value)


class PreservedTaskRef(BaseModel):
    """Reference to a previous task that should be preserved unchanged."""

    kind: Literal["preserved"] = "preserved"
    previous_index: int
    depends_on: list[int]


class SpecTaskPlan(BaseModel):
    tasks: list[Annotated[PlannerTask | PreservedTaskRef, Field(discriminator="kind")]]


class PlanTask(BaseModel):
    id: str
    spec: str
    title: str
    description: str
    outputs: list[str]
    depends_on: list[str]
    spec_refs: list[str]
    arch_refs: list[str]
    status: TaskStatus = TaskStatus.PENDING
    verify: list[str]
    inject_files: list[str] = []
    cross_spec_interfaces: list[str] = []
    context_files: list[str] = []
    notes: str = ""

    @field_validator("verify", mode="before")
    @classmethod
    def _normalize_verify(cls, value: Any) -> list[str]:
        return _coerce_verify(value)


class PlanMeta(BaseModel):
    generated_at: str
    total_tasks: int
    specs: list[str]


class Plan(BaseModel):
    meta: PlanMeta
    tasks: list[PlanTask]
