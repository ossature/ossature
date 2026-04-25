from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TaskStatus(Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL = "manual"


class PlannerTask(BaseModel):
    kind: Literal["task"] = "task"
    title: str
    description: str
    outputs: list[str]
    depends_on: list[int]
    spec_refs: list[str]
    arch_refs: list[str]
    verify: str
    context_files: list[str] = []


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
    verify: str
    inject_files: list[str] = []
    cross_spec_interfaces: list[str] = []
    context_files: list[str] = []
    notes: str = ""


class PlanMeta(BaseModel):
    generated_at: str
    total_tasks: int
    specs: list[str]


class Plan(BaseModel):
    meta: PlanMeta
    tasks: list[PlanTask]
