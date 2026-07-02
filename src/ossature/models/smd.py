from dataclasses import dataclass, field
from enum import Enum

from ossature.models.shared import Status


class Priority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Requirement:
    """One H3 requirement.

    anchor is an optional stable slug from a trailing {#slug} marker on the
    heading, giving VMD @covers targets and plan-task covers a reference
    that survives heading renames. no_verify marks a requirement as
    intentionally unverified (a {.no-verify} marker) so coverage reporting
    skips it on purpose.
    """

    title: str
    description: str
    accepts: str
    returns: str
    errors: list[tuple[str, str]] = field(default_factory=list)
    anchor: str = ""
    no_verify: bool = False


@dataclass
class Example:
    name: str
    input: str
    output: str


@dataclass
class SMDSpec:
    title: str
    spec_id: str
    status: Status
    priority: Priority
    overview: str
    depends: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    notes: str = ""
