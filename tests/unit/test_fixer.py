from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_smd
from pydantic_ai import ModelRetry
from rich.console import Console
from rich.status import Status

from ossature.audit.fixer import (
    FixContext,
    _build_cross_spec_finding_prompt,
    _build_finding_prompt,
    _resolve_spec_sandboxed,
    _verify_spec_parses,
    fix_cross_spec_findings,
    fix_spec_findings,
)
from ossature.cli.commands.audit import (
    _build_amd_file_map,
    _build_spec_file_map,
    _has_fixable_errors,
)
from ossature.config.loader import AuditConfig, LLMConfig, OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.audit import (
    AuditFinding,
    CrossSpecAuditReport,
    CrossSpecFinding,
    Severity,
    SpecAuditReport,
)
from ossature.models.shared import Status as SpecStatus
from ossature.shared.llm import UsageTracker

# -- Minimal valid spec fixtures --

VALID_SMD = """\
---
id: TEST
status: draft
priority: high
depends: []
---

# Test Spec

## Overview

A test specification for unit testing.

## Goals

- Be testable

## Non-Goals

- Be production-ready

## Requirements

### Do Something

Does something useful.

**Accepts:** A string input

**Returns:** A string output

**Errors:**

- Empty input → Return error message

## Constraints

- Must be fast

## Examples

### Basic Example

**Input:**

```
hello
```

**Output:**

```
world
```

## Acceptance Criteria

- It works

## Notes

Nothing special.
"""

VALID_AMD = """\
---
spec: TEST
status: draft
---

# Architecture: Test

## Overview

Architecture for the test spec.

## Components

### TestComponent

@path: src/test.py

The main test component.

**Interface:**

```python
def do_something(input: str) -> str: ...
```

**Contracts:** None

## Notes

Nothing special.
"""


@pytest.fixture
def quiet_console() -> Console:
    return Console(quiet=True)


@pytest.fixture
def quiet_status(quiet_console: Console) -> Status:
    return Status("test", console=quiet_console)


class TestResolveSpecSandboxed:
    def test_simple_relative_path(self, tmp_path: Path) -> None:
        result = _resolve_spec_sandboxed(tmp_path, "specs/auth.smd")
        assert result == tmp_path / "specs" / "auth.smd"

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_spec_sandboxed(tmp_path, "../etc/passwd")

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_spec_sandboxed(tmp_path, "/etc/passwd")

    def test_rejects_deep_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_spec_sandboxed(tmp_path, "specs/../../etc/shadow")


class TestFixContext:
    def test_resolves_spec_dir(
        self, tmp_path: Path, quiet_console: Console, quiet_status: Status
    ) -> None:
        ctx = FixContext(spec_dir=tmp_path / "specs", console=quiet_console, status=quiet_status)
        assert ctx.spec_dir == (tmp_path / "specs").resolve()

    def test_edited_files_starts_empty(
        self, tmp_path: Path, quiet_console: Console, quiet_status: Status
    ) -> None:
        ctx = FixContext(spec_dir=tmp_path, console=quiet_console, status=quiet_status)
        assert ctx.edited_files == []


class TestVerifySpecParses:
    def test_valid_smd_passes(self, tmp_path: Path) -> None:
        smd_file = tmp_path / "test.smd"
        smd_file.write_text(VALID_SMD)
        assert _verify_spec_parses(smd_file) is True

    def test_invalid_smd_fails(self, tmp_path: Path) -> None:
        smd_file = tmp_path / "test.smd"
        smd_file.write_text("not a valid spec")
        assert _verify_spec_parses(smd_file) is False

    def test_valid_amd_passes(self, tmp_path: Path) -> None:
        amd_file = tmp_path / "test.amd"
        amd_file.write_text(VALID_AMD)
        assert _verify_spec_parses(amd_file) is True

    def test_invalid_amd_fails(self, tmp_path: Path) -> None:
        amd_file = tmp_path / "test.amd"
        amd_file.write_text("not a valid architecture")
        assert _verify_spec_parses(amd_file) is False


class TestBuildFindingPrompt:
    def test_includes_all_finding_fields(self) -> None:
        finding = AuditFinding(
            severity=Severity.WARNING,
            location="Requirements > Init Command",
            issue="Missing error case",
            suggestion="Add an error for missing argument",
        )
        prompt = _build_finding_prompt(finding, "specs/cli.smd")
        assert "WARNING" in prompt
        assert "Requirements > Init Command" in prompt
        assert "Missing error case" in prompt
        assert "Add an error for missing argument" in prompt
        assert "specs/cli.smd" in prompt

    def test_includes_target_file(self) -> None:
        finding = AuditFinding(
            severity=Severity.ERROR,
            location="Overview",
            issue="Ambiguous",
            suggestion="Clarify",
        )
        prompt = _build_finding_prompt(finding, "auth.smd")
        assert "<target_files>" in prompt
        assert "- `auth.smd` (the spec)" in prompt
        assert "architecture" not in prompt

    def test_includes_amd_files_with_hint(self) -> None:
        finding = AuditFinding(
            severity=Severity.ERROR,
            location="Components > TokenService",
            issue="Contract conflicts with requirement",
            suggestion="Drop the conflicting contract",
        )
        prompt = _build_finding_prompt(finding, "auth.smd", ["auth.amd", "auth-models.amd"])
        assert "- `auth.smd` (the spec)" in prompt
        assert "- `auth.amd` (architecture)" in prompt
        assert "- `auth-models.amd` (architecture)" in prompt
        assert "usually live in the architecture file" in prompt


class TestBuildCrossSpecFindingPrompt:
    def test_includes_all_spec_files(self) -> None:
        finding = CrossSpecFinding(
            severity=Severity.WARNING,
            specs=["AUTH", "API"],
            issue="Contract mismatch",
            suggestion="Align types",
        )
        spec_files = {"AUTH": "specs/auth.smd", "API": "specs/api.smd"}
        prompt = _build_cross_spec_finding_prompt(finding, spec_files)
        assert "AUTH" in prompt
        assert "API" in prompt
        assert "specs/auth.smd" in prompt
        assert "specs/api.smd" in prompt
        assert "Contract mismatch" in prompt
        assert "Align types" in prompt

    def test_with_filtered_spec_files(self) -> None:
        finding = CrossSpecFinding(
            severity=Severity.INFO,
            specs=["AUTH"],
            issue="Minor issue",
            suggestion="Fix it",
        )
        # Caller is responsible for filtering — only pass relevant files
        relevant_files = {"AUTH": "specs/auth.smd"}
        prompt = _build_cross_spec_finding_prompt(finding, relevant_files)
        assert "specs/auth.smd" in prompt
        assert "specs/api.smd" not in prompt


class TestHasFixableErrors:
    def test_no_findings(self) -> None:
        report = SpecAuditReport(findings=[])
        assert _has_fixable_errors(report) is False

    def test_error_with_suggestion(self) -> None:
        report = SpecAuditReport(
            findings=[
                AuditFinding(
                    severity=Severity.ERROR,
                    location="Overview",
                    issue="Ambiguous",
                    suggestion="Clarify the requirement",
                )
            ]
        )
        assert _has_fixable_errors(report) is True

    def test_warning_with_suggestion_is_not_fixable(self) -> None:
        report = SpecAuditReport(
            findings=[
                AuditFinding(
                    severity=Severity.WARNING,
                    location="Overview",
                    issue="Ambiguous",
                    suggestion="Clarify the requirement",
                )
            ]
        )
        assert _has_fixable_errors(report) is False

    def test_error_without_suggestion(self) -> None:
        report = SpecAuditReport(
            findings=[
                AuditFinding(
                    severity=Severity.ERROR,
                    location="Overview",
                    issue="Minor note",
                    suggestion="",
                )
            ]
        )
        assert _has_fixable_errors(report) is False

    def test_cross_spec_error(self) -> None:
        report = CrossSpecAuditReport(
            findings=[
                CrossSpecFinding(
                    severity=Severity.ERROR,
                    specs=["AUTH", "API"],
                    issue="Mismatch",
                    suggestion="Align the types",
                )
            ]
        )
        assert _has_fixable_errors(report) is True

    def test_cross_spec_warning_is_not_fixable(self) -> None:
        report = CrossSpecAuditReport(
            findings=[
                CrossSpecFinding(
                    severity=Severity.WARNING,
                    specs=["AUTH", "API"],
                    issue="Mismatch",
                    suggestion="Align the types",
                )
            ]
        )
        assert _has_fixable_errors(report) is False

    def test_mixed_findings_only_counts_errors(self) -> None:
        report = SpecAuditReport(
            findings=[
                AuditFinding(
                    severity=Severity.INFO,
                    location="A",
                    issue="No fix",
                    suggestion="",
                ),
                AuditFinding(
                    severity=Severity.WARNING,
                    location="B",
                    issue="Has fix",
                    suggestion="Do this",
                ),
                AuditFinding(
                    severity=Severity.ERROR,
                    location="C",
                    issue="Real error",
                    suggestion="Fix it",
                ),
            ]
        )
        assert _has_fixable_errors(report) is True


class TestBuildSpecFileMap:
    def test_maps_spec_ids_to_relative_paths(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()

        smd_files = [spec_dir / "auth.smd", spec_dir / "api.smd"]
        parsed_smds = [make_smd("AUTH"), make_smd("API")]

        result = _build_spec_file_map(smd_files, [], parsed_smds, [], spec_dir)
        assert result == {"AUTH": "auth.smd", "API": "api.smd"}

    def test_nested_spec_paths(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "specs"
        (spec_dir / "sub").mkdir(parents=True)

        smd_files = [spec_dir / "sub" / "auth.smd"]
        parsed_smds = [make_smd("AUTH")]

        result = _build_spec_file_map(smd_files, [], parsed_smds, [], spec_dir)
        assert result == {"AUTH": "sub/auth.smd"}


class TestBuildAmdFileMap:
    def test_maps_spec_ids_to_amd_files(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()

        amd_files = [spec_dir / "auth.amd", spec_dir / "auth-models.amd"]
        parsed_amds = [
            AMDSpec(title="Auth Arch", spec_id="AUTH", status=SpecStatus.DRAFT, overview="..."),
            AMDSpec(title="Auth Models", spec_id="AUTH", status=SpecStatus.DRAFT, overview="..."),
        ]

        result = _build_amd_file_map(amd_files, parsed_amds, spec_dir)
        assert result == {"AUTH": ["auth.amd", "auth-models.amd"]}


@pytest.fixture
def fixer_config(tmp_path: Path) -> OssatureConfig:
    return OssatureConfig(root=tmp_path, llm=LLMConfig(model="test:mock"))


def _make_mock_agent():
    agent = MagicMock()
    result = MagicMock()
    result.usage.return_value = MagicMock(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        requests=1,
    )
    agent.run_sync.return_value = result
    agent._mock_result = result
    return agent


@pytest.fixture
def mock_fixer_agent():
    with (
        patch("ossature.audit.fixer._create_fixer_agent") as mock_create,
        patch("ossature.audit.fixer._verify_spec_parses", return_value=True),
    ):
        agent = _make_mock_agent()
        mock_create.return_value = agent
        yield agent


@pytest.fixture
def mock_fixer_agent_bad_parse():
    with (
        patch("ossature.audit.fixer._create_fixer_agent") as mock_create,
        patch("ossature.audit.fixer._verify_spec_parses", return_value=False),
    ):
        agent = _make_mock_agent()
        mock_create.return_value = agent
        yield agent


class TestFixSpecFindings:
    def test_skips_info_when_errors_present(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_file = "test.smd"
        (spec_dir / spec_file).write_text(VALID_SMD)

        fix_spec_findings(
            findings=[
                AuditFinding(severity=Severity.ERROR, location="A", issue="bad", suggestion="fix"),
                AuditFinding(severity=Severity.INFO, location="B", issue="note", suggestion="nah"),
            ],
            spec_file=spec_file,
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
        )

        assert mock_fixer_agent.run_sync.call_count == 1

    def test_reverts_when_parse_fails(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent_bad_parse,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_file = "test.smd"
        (spec_dir / spec_file).write_text(VALID_SMD)

        edited = fix_spec_findings(
            findings=[
                AuditFinding(severity=Severity.ERROR, location="A", issue="bad", suggestion="fix"),
            ],
            spec_file=spec_file,
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
        )

        assert edited == []
        assert (spec_dir / spec_file).read_text() == VALID_SMD

    def test_collects_edited_files_on_success(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_file = "test.smd"
        (spec_dir / spec_file).write_text(VALID_SMD)

        mock_result = mock_fixer_agent._mock_result

        def fake_run_sync(prompt, *, deps, **kwargs):
            deps.edited_files.append(spec_file)
            return mock_result

        mock_fixer_agent.run_sync.side_effect = fake_run_sync

        edited = fix_spec_findings(
            findings=[
                AuditFinding(severity=Severity.ERROR, location="A", issue="bad", suggestion="fix"),
            ],
            spec_file=spec_file,
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
        )

        assert edited == [spec_file]

    def test_amd_file_listed_in_prompt(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "test.smd").write_text(VALID_SMD)
        (spec_dir / "test.amd").write_text(VALID_AMD)

        fix_spec_findings(
            findings=[
                AuditFinding(
                    severity=Severity.ERROR,
                    location="Components > TestComponent",
                    issue="contract conflict",
                    suggestion="fix",
                ),
            ],
            spec_file="test.smd",
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
            amd_files=["test.amd"],
        )

        prompt = mock_fixer_agent.run_sync.call_args[0][0]
        assert "- `test.smd` (the spec)" in prompt
        assert "- `test.amd` (architecture)" in prompt

    def test_reverts_all_files_when_amd_edit_breaks_parsing(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent_bad_parse,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "test.smd").write_text(VALID_SMD)
        (spec_dir / "test.amd").write_text(VALID_AMD)

        mock_result = mock_fixer_agent_bad_parse._mock_result

        def fake_run_sync(prompt, *, deps, **kwargs):
            (spec_dir / "test.amd").write_text("broken")
            deps.edited_files.append("test.amd")
            return mock_result

        mock_fixer_agent_bad_parse.run_sync.side_effect = fake_run_sync

        edited = fix_spec_findings(
            findings=[
                AuditFinding(
                    severity=Severity.ERROR,
                    location="Components > TestComponent",
                    issue="contract conflict",
                    suggestion="fix",
                ),
            ],
            spec_file="test.smd",
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
            amd_files=["test.amd"],
        )

        assert edited == []
        assert (spec_dir / "test.amd").read_text() == VALID_AMD
        assert (spec_dir / "test.smd").read_text() == VALID_SMD


class TestFixCrossSpecFindings:
    def test_reverts_all_when_parse_fails(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent_bad_parse,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "auth.smd").write_text(VALID_SMD)
        (spec_dir / "api.smd").write_text(VALID_SMD)

        mock_result = mock_fixer_agent_bad_parse._mock_result

        def fake_run_sync(prompt, *, deps, **kwargs):
            deps.edited_files.append("auth.smd")
            return mock_result

        mock_fixer_agent_bad_parse.run_sync.side_effect = fake_run_sync

        edited = fix_cross_spec_findings(
            findings=[
                CrossSpecFinding(
                    severity=Severity.ERROR,
                    specs=["AUTH", "API"],
                    issue="mismatch",
                    suggestion="align",
                ),
            ],
            spec_files={"AUTH": "auth.smd", "API": "api.smd"},
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
        )

        assert edited == []
        assert (spec_dir / "auth.smd").read_text() == VALID_SMD

    def test_collects_edited_files_on_success(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "auth.smd").write_text(VALID_SMD)

        mock_result = mock_fixer_agent._mock_result

        def fake_run_sync(prompt, *, deps, **kwargs):
            deps.edited_files.append("auth.smd")
            return mock_result

        mock_fixer_agent.run_sync.side_effect = fake_run_sync

        edited = fix_cross_spec_findings(
            findings=[
                CrossSpecFinding(
                    severity=Severity.ERROR,
                    specs=["AUTH"],
                    issue="mismatch",
                    suggestion="align",
                ),
            ],
            spec_files={"AUTH": "auth.smd"},
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
        )

        assert edited == ["auth.smd"]

    def test_tracks_usage_when_tracker_provided(
        self,
        tmp_path: Path,
        quiet_console: Console,
        quiet_status: Status,
        fixer_config,
        mock_fixer_agent,
    ) -> None:
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "auth.smd").write_text(VALID_SMD)

        mock_result = mock_fixer_agent._mock_result

        def fake_run_sync(prompt, *, deps, **kwargs):
            deps.edited_files.append("auth.smd")
            return mock_result

        mock_fixer_agent.run_sync.side_effect = fake_run_sync

        tracker = UsageTracker()
        fix_cross_spec_findings(
            findings=[
                CrossSpecFinding(
                    severity=Severity.ERROR,
                    specs=["AUTH"],
                    issue="mismatch",
                    suggestion="align",
                ),
            ],
            spec_files={"AUTH": "auth.smd"},
            spec_dir=spec_dir,
            config=fixer_config,
            console=quiet_console,
            status=quiet_status,
            tracker=tracker,
        )

        assert tracker.requests == 1


class TestAuditConfigDefaults:
    def test_max_fix_cycles_default(self) -> None:
        cfg = AuditConfig()
        assert cfg.max_fix_cycles == 3
