from dataclasses import dataclass, field
from typing import Any

from ossature.models.shared import Status


@dataclass
class Fixture:
    """A named value declared once and reused across case rows.

    A value fixture substitutes for a whole input column. An opaque fixture
    names a parameter the harness constructs fresh for every case (a database
    handle, an allocator); it consumes no column and carries only a free-text
    constructor label.
    """

    name: str
    opaque: bool = False
    label: str = ""
    value: Any = None
    raw: str = ""
    line: int = field(default=0, compare=False)


@dataclass
class Param:
    """One parameter of a value group's signature.

    type is empty or one of the blessed coercion types (currently only
    "decimal"). opaque_fixture holds the fixture name when the parameter is
    supplied by an opaque fixture instead of a row column.
    """

    name: str
    type: str = ""
    opaque_fixture: str = ""


@dataclass
class ValueCase:
    """One row of a value group: inputs in, expected result out.

    expect_kind is "value" (compare against expected), "error" (the call
    must raise error_type, optionally with error_message contained in the
    message), or "ok" (the call must not error; the value is not compared).
    Raw cell text is kept alongside parsed values so decimal columns and
    canonical serialization preserve exactly what the author wrote.
    """

    name: str
    inputs: list[Any] = field(default_factory=list)
    raw_inputs: list[str] = field(default_factory=list)
    expect_kind: str = "value"
    expected: Any = None
    raw_expected: str = ""
    error_type: str = ""
    error_message: str = ""
    line: int = field(default=0, compare=False)


@dataclass
class CliCase:
    """One row of a cli group: a single terminating command invocation.

    A None channel is not checked. stdout/stderr compare exactly unless the
    cell used a '~matches' prefix, in which case the value is a regex
    pattern searched against the captured text. argv entries are strings,
    except non-UTF-8 byte literals which parse to bytes.
    """

    name: str
    argv: list[Any] = field(default_factory=list)
    stdout: str | None = None
    stdout_is_pattern: bool = False
    exit_code: int | None = None
    stderr: str | None = None
    stderr_is_pattern: bool = False
    line: int = field(default=0, compare=False)


@dataclass
class Group:
    """A blank-line-separated block: one callable (or program) under test.

    kind is "value" (function rows) or "cli" (command rows). compare_modes
    holds the normalized mode tokens from the signature line (approx,
    unordered, matches, struct, decimal). covers lists the requirement
    targets declared with @covers, either heading anchors or quoted heading
    text, unresolved at parse time.
    """

    name: str
    kind: str = "value"
    params: list[Param] = field(default_factory=list)
    returns: str = ""
    compare_modes: list[str] = field(default_factory=list)
    approx_tol: float | None = None
    covers: list[str] = field(default_factory=list)
    cases: list[ValueCase] = field(default_factory=list)
    cli_cases: list[CliCase] = field(default_factory=list)
    line: int = field(default=0, compare=False)

    @property
    def arity(self) -> int:
        """Input columns a row must carry: params not fed by opaque fixtures."""
        return sum(1 for p in self.params if not p.opaque_fixture)

    @property
    def case_names(self) -> list[str]:
        if self.kind == "cli":
            return [c.name for c in self.cli_cases]
        return [c.name for c in self.cases]


@dataclass
class VMDSpec:
    """A parsed verification file: author-written cases for one spec."""

    spec_id: str
    arch_id: str = ""
    status: Status = Status.DRAFT
    fixtures: list[Fixture] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    # Non-fatal parse diagnostics, excluded from equality so round-trips
    # compare on content only (same treatment as AMDSpec.warnings).
    warnings: list[str] = field(default_factory=list, compare=False)
