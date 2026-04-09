import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from pydantic_ai import Agent as _Agent
from pydantic_ai.usage import RunUsage

from ossature.cli.main import cli
from ossature.models.audit import AuditFinding, CrossSpecAuditReport, SpecAuditReport
from ossature.models.plan import PlannerTask, SpecTaskPlan

# Templates

MINIMAL_SMD = """\
# {title}

@id: {spec_id}
@status: draft
@priority: high
@depends: [{depends}]

## Overview

{overview}

## Goals

- Implement {title} functionality

## Non-Goals

- Out of scope features

## Requirements

### Core Requirement

Core requirement description.

**Accepts:** input data

**Returns:** processed output

## Constraints

- Must be well-structured

## Examples

### Basic Example

**Input:**

```
sample input
```

**Output:**

```
sample output
```

## Acceptance Criteria

- [ ] All tests pass
"""


# Helpers


def write_smd(
    project_dir: Path, spec_id: str, title: str, overview: str = "Overview text.", depends: str = ""
) -> Path:
    filename = f"{spec_id.lower()}.smd"
    filepath = project_dir / "specs" / filename
    filepath.write_text(
        MINIMAL_SMD.format(
            title=title,
            spec_id=spec_id,
            overview=overview,
            depends=depends,
        )
    )
    return filepath


def make_spec_task_plan(tasks: list[dict]) -> SpecTaskPlan:
    return SpecTaskPlan(
        tasks=[
            PlannerTask(
                title=t["title"],
                description=t.get("description", ""),
                outputs=t.get("outputs", []),
                depends_on=t.get("depends_on", []),
                spec_refs=t.get("spec_refs", []),
                arch_refs=t.get("arch_refs", []),
                verify=t.get("verify", ""),
            )
            for t in tasks
        ]
    )


def run_in_project(runner: CliRunner, project_dir: Path, args: list[str], input: str | None = None):
    old_cwd = os.getcwd()
    os.chdir(project_dir)
    try:
        return runner.invoke(cli, args, catch_exceptions=False, input=input)
    finally:
        os.chdir(old_cwd)


# Mock helpers


def _make_mock_run_sync(
    spec_plans: dict[str, SpecTaskPlan],
    audit_findings: list[AuditFinding] | None = None,
):
    _mock_usage = RunUsage(input_tokens=0, output_tokens=0, requests=1)
    _audit_call_count: dict[str, int] = {}

    def mock_run_sync(self, prompt, *args, **kwargs):
        result = MagicMock()
        result.usage.return_value = _mock_usage

        # Planner agent: output_type is SpecTaskPlan
        if getattr(self, "_output_type", None) is SpecTaskPlan:
            for spec_id, plan in spec_plans.items():
                if f"@id: {spec_id}" in prompt:
                    result.output = plan
                    return result
            result.output = next(iter(spec_plans.values()))
            return result

        # Audit agent: output_type is SpecAuditReport
        if getattr(self, "_output_type", None) is SpecAuditReport:
            # Return findings on first call per spec, empty on re-audit
            if audit_findings:
                key = "audit"
                for spec_id in spec_plans:
                    if f"@id: {spec_id}" in prompt:
                        key = spec_id
                        break
                count = _audit_call_count.get(key, 0)
                _audit_call_count[key] = count + 1
                if count == 0:
                    result.output = SpecAuditReport(findings=audit_findings)
                    return result
            result.output = SpecAuditReport(findings=[])
            return result

        # Cross-spec audit: output_type is CrossSpecAuditReport
        if getattr(self, "_output_type", None) is CrossSpecAuditReport:
            result.output = CrossSpecAuditReport(findings=[])
            return result

        # Brief / interface / fixer: returns a string
        result.output = "Mock brief or interface content."
        return result

    return mock_run_sync


_real_agent_init = _Agent.__init__


def _mock_agent_init(self, *args, **kwargs):
    kwargs["defer_model_check"] = True
    _real_agent_init(self, *args, **kwargs)
    self._output_type = kwargs.get("output_type")


def patch_all_agents(
    spec_plans: dict[str, SpecTaskPlan],
    audit_findings: list[AuditFinding] | None = None,
):
    stack = ExitStack()
    stack.enter_context(patch("pydantic_ai.Agent.__init__", _mock_agent_init))
    stack.enter_context(
        patch(
            "pydantic_ai.Agent.run_sync",
            _make_mock_run_sync(spec_plans, audit_findings=audit_findings),
        )
    )
    return stack
