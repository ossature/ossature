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
class Group:
    """A table group: one callable under test, one example row per case.

    This is the outline form: state the behavior once in the signature,
    feed it many data rows. compare_modes holds the normalized mode tokens
    from the signature line (approx, unordered, matches, struct, decimal).
    covers lists the requirement targets declared with @covers, either
    heading anchors or quoted heading text, unresolved at parse time.
    """

    name: str
    kind: str = "value"
    params: list[Param] = field(default_factory=list)
    returns: str = ""
    compare_modes: list[str] = field(default_factory=list)
    approx_tol: float | None = None
    covers: list[str] = field(default_factory=list)
    cases: list[ValueCase] = field(default_factory=list)
    line: int = field(default=0, compare=False)

    @property
    def arity(self) -> int:
        """Input columns a row must carry: params not fed by opaque fixtures."""
        return sum(1 for p in self.params if not p.opaque_fixture)

    @property
    def case_names(self) -> list[str]:
        return [c.name for c in self.cases]


@dataclass
class GivenBinding:
    """One `given` step: a named literal, or a fixture reference.

    With `given name = <JSON>` the value is the literal. With
    `given NAME` the binding references an @fixture; opaque fixtures parse
    but mark the scenario as not yet runnable by the deterministic harness.
    """

    name: str
    value: Any = None
    raw: str = ""
    fixture: str = ""
    opaque: bool = False
    line: int = field(default=0, compare=False)


@dataclass
class CallStep:
    """A `when target(args)` step with its single outcome assertion.

    Arguments are resolved at parse time: JSON literals stay themselves and
    given/fixture names substitute their values. expect_kind mirrors
    ValueCase: "value" (then returns), "error" (then raises), or "ok".
    """

    target: str
    args: list[Any] = field(default_factory=list)
    raw_args: list[str] = field(default_factory=list)
    expect_kind: str = "value"
    expected: Any = None
    raw_expected: str = ""
    error_type: str = ""
    error_message: str = ""


@dataclass
class CommandStep:
    """A `when $ command` step with its channel assertions.

    argv entries are strings, or bytes when the word carried a \\xNN escape.
    stdout_lines holds verbatim `>` output lines (exact match over the whole
    stream, one trailing newline tolerated); stdout/stderr carry keyword
    checks where mode is "has", "is", "matches", or "empty". The exit code
    defaults to 0: a scenario is a successful session unless it says
    otherwise.
    """

    argv: list[Any] = field(default_factory=list)
    stdout_lines: list[str] | None = None
    stdout: str | None = None
    stdout_mode: str = ""
    exit_code: int = 0
    stderr: str | None = None
    stderr_mode: str = ""


@dataclass
class Scenario:
    """The narrative form: one named behavior, arranged and asserted.

    kind is "call" (one CallStep) or "command" (one or more CommandStep in
    sequence, sharing a per-scenario working directory). The name is free
    words and doubles as documentation; slug is its normalized form and is
    the stable case identity build state keys on.
    """

    name: str
    slug: str
    kind: str = ""
    covers: list[str] = field(default_factory=list)
    givens: list[GivenBinding] = field(default_factory=list)
    call: CallStep | None = None
    steps: list[CommandStep] = field(default_factory=list)
    line: int = field(default=0, compare=False)

    @property
    def uses_opaque(self) -> bool:
        return any(g.opaque for g in self.givens)


@dataclass
class VMDSpec:
    """A parsed verification file: author-written cases for one spec."""

    spec_id: str
    arch_id: str = ""
    status: Status = Status.DRAFT
    fixtures: list[Fixture] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
