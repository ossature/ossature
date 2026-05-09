import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from ossature.config.loader import BuildConfig, OssatureConfig, OutputConfig
from ossature.models.plan import Plan, PlanMeta, PlanTask, TaskStatus
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec
from ossature.templates.manager import TemplateManager

MINIMAL_CONFIG = """\
[project]
name = "testapp"
version = "0.1.0"
spec_dir = "specs"
context_dir = "context"

[output]
dir = "output"
language = "python"

[llm]
model = "test:mock-model"
"""


@pytest.fixture
def temp_dir() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def initialized_project(temp_dir: Path) -> Path:
    manager = TemplateManager(temp_dir)
    manager.init_project(name="test-project")
    return temp_dir


@pytest.fixture
def sample_config(temp_dir: Path) -> OssatureConfig:
    return OssatureConfig(
        name="test-project",
        version="0.0.1",
        root=temp_dir,
        spec_dir="specs",
        output=OutputConfig(language="python"),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(temp_dir: Path) -> Path:
    root = temp_dir / "testapp"
    root.mkdir()
    (root / "specs").mkdir()
    (root / "context").mkdir()
    (root / "output").mkdir()
    (root / "ossature.toml").write_text(MINIMAL_CONFIG)
    return root


def make_config(
    root: Path,
    language: str = "python",
    output_dir: str = "output",
    setup: str | list[str] | None = None,
    verify: str | list[str] | None = None,
    test: str | list[str] | None = None,
) -> OssatureConfig:
    def _to_list(value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        return list(value)

    return OssatureConfig(
        name="test",
        version="0.0.1",
        root=root,
        output=OutputConfig(dir=output_dir, language=language),
        build=BuildConfig(setup=_to_list(setup), verify=_to_list(verify), test=_to_list(test)),
    )


def make_smd(spec_id: str, depends: list[str] | None = None) -> SMDSpec:
    return SMDSpec(
        title=f"{spec_id} Module",
        spec_id=spec_id,
        status=Status.DRAFT,
        priority=Priority.HIGH,
        overview=f"Overview of {spec_id}",
        depends=depends or [],
    )


def make_task(
    id: str,
    spec: str,
    outputs: list[str] | None = None,
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
    verify: str | list[str] = "",
) -> PlanTask:
    return PlanTask(
        id=id,
        spec=spec,
        title=f"{spec} task {id}",
        description="",
        outputs=outputs or [],
        depends_on=depends_on or [],
        spec_refs=[],
        arch_refs=[],
        status=status,
        verify=verify,
    )


def make_plan(tasks: list[PlanTask]) -> Plan:
    specs = sorted({t.spec for t in tasks})
    return Plan(
        meta=PlanMeta(
            generated_at="2026-01-01T00:00:00Z",
            total_tasks=len(tasks),
            specs=specs,
        ),
        tasks=tasks,
    )
