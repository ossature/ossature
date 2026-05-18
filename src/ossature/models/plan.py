import re
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


_SOURCE_SCHEME_PATTERN = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://")


def _normalize_source(value: Any) -> list[str]:
    """Normalize a `source` field to a list of context-relative path patterns.

    Accepts None, a single string, or a list of strings. Each entry may
    optionally use a `context://` URL-scheme prefix; the prefix is stripped
    here so internal callers always see plain context-relative paths.
    Other schemes are rejected, as are absolute paths and parent-directory
    traversal.
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [str(item) for item in value]
    else:
        raise TypeError(f"source must be a string or a list of strings, got {type(value).__name__}")

    normalized: list[str] = []
    for raw in items:
        if not raw:
            raise ValueError("source entries must be non-empty strings")
        scheme_match = _SOURCE_SCHEME_PATTERN.match(raw)
        if scheme_match:
            scheme = scheme_match.group(1)
            if scheme != "context":
                raise ValueError(
                    f"source scheme {scheme!r} is not supported; only 'context://' is allowed"
                )
            path = raw[scheme_match.end() :]
        else:
            path = raw
        if not path:
            raise ValueError(f"source entry {raw!r} has no path after the scheme prefix")
        if path.startswith("/"):
            raise ValueError(f"source entry {raw!r} must be a context-relative path, not absolute")
        parts = path.replace("\\", "/").split("/")
        if any(part == ".." for part in parts):
            raise ValueError(f"source entry {raw!r} must not contain '..' path traversal segments")
        normalized.append(path)
    return normalized


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
    source: list[str] = []

    @field_validator("verify", mode="before")
    @classmethod
    def _normalize_verify(cls, value: Any) -> list[str]:
        return _coerce_verify(value)

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: Any) -> list[str]:
        return _normalize_source(value)


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
    source: list[str] = []
    notes: str = ""

    @field_validator("verify", mode="before")
    @classmethod
    def _normalize_verify(cls, value: Any) -> list[str]:
        return _coerce_verify(value)

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: Any) -> list[str]:
        return _normalize_source(value)


class PlanMeta(BaseModel):
    generated_at: str
    total_tasks: int
    specs: list[str]


class Plan(BaseModel):
    meta: PlanMeta
    tasks: list[PlanTask]
