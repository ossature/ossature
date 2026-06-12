from pathlib import Path

import pytest

from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.shared import Status
from ossature.renderer.amd import (
    render_amd,
    render_component,
    render_data_model,
    render_dependency,
    save_amd,
)


class TestAMDRenderer:
    def test_render_component_with_language(self):
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="class API: ...",
            interface_language="python",
        )
        result = render_component(component)
        assert "```python" in result
        assert "class API: ..." in result

    def test_render_component_without_language(self):
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="class API: ...",
            interface_language="",
        )
        result = render_component(component)
        lines = result.split("\n")
        fence_lines = [line for line in lines if line.startswith("```")]
        assert fence_lines[0] == "```"
        assert "class API: ..." in result

    def test_render_component_with_depends_on(self):
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="def run(): ...",
            depends_on=["A", "B"],
        )
        result = render_component(component)
        assert "**Depends on:** A, B" in result

    def test_render_component_without_depends_on(self):
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="def run(): ...",
            depends_on=[],
        )
        result = render_component(component)
        assert "**Depends on:**" not in result

    def test_render_component_with_contracts(self):
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="def run(): ...",
            contracts=["Returns within 100ms", "Never mutates the input"],
        )
        result = render_component(component)
        assert "**Contracts:**" in result
        assert "- Returns within 100ms" in result
        assert "- Never mutates the input" in result
        # Contracts render after the interface block, before depends-on.
        assert result.index("```") < result.index("**Contracts:**")

    def test_render_component_without_contracts(self):
        # An empty list renders as an explicit None so the output satisfies
        # the parser's required-contracts rule.
        component = Component(
            name="API",
            path="src/api.py",
            description="The API.",
            interface="def run(): ...",
            contracts=[],
        )
        result = render_component(component)
        assert "**Contracts:** None" in result

    def test_render_data_model_with_language(self):
        model = DataModel(
            name="Users",
            definition="CREATE TABLE users (id INT);",
            definition_language="sql",
        )
        result = render_data_model(model)
        assert "```sql" in result
        assert "CREATE TABLE users" in result

    def test_render_data_model_without_language(self):
        model = DataModel(
            name="Users",
            definition="CREATE TABLE users (id INT);",
            definition_language="",
        )
        result = render_data_model(model)
        lines = result.split("\n")
        fence_lines = [line for line in lines if line.startswith("```")]
        assert fence_lines[0] == "```"

    def test_render_dependency(self):
        dep = Dependency(name="redis", purpose="Caching layer")
        result = render_dependency(dep)
        assert result == "- redis: Caching layer"

    def test_render_amd_full(self):
        spec = AMDSpec(
            title="Test System",
            spec_id="SMD-001",
            status=Status.DRAFT,
            overview="Overview text.",
            components=[
                Component(
                    name="Comp",
                    path="src/comp.py",
                    description="A component.",
                    interface="def run(): ...",
                    interface_language="python",
                    depends_on=["DB"],
                ),
            ],
            data_models=[
                DataModel(
                    name="Record",
                    definition="CREATE TABLE t (id INT);",
                    definition_language="sql",
                ),
            ],
            flow="A -> B -> C",
            dependencies=[
                Dependency(name="postgres", purpose="Primary store"),
            ],
            notes="Some notes.",
        )
        result = render_amd(spec)
        assert "# Architecture: Test System" in result
        assert result.startswith("---\n")
        assert "spec: SMD-001" in result
        assert "status: draft" in result
        assert "## Overview" in result
        assert "Overview text." in result
        assert "## Components" in result
        assert "### Comp" in result
        assert "## Data Models" in result
        assert "### Record" in result
        assert "## Flow" in result
        assert "A -> B -> C" in result
        assert "## Dependencies" in result
        assert "- postgres: Primary store" in result
        assert "## Notes" in result
        assert "Some notes." in result

    def test_render_amd_empty_optional_sections(self):
        spec = AMDSpec(
            title="Minimal",
            spec_id="SMD-002",
            status=Status.DRAFT,
            overview="Overview.",
            components=[
                Component(
                    name="Comp",
                    path="src/c.py",
                    description="Desc.",
                    interface="def f(): ...",
                ),
            ],
            data_models=[],
            flow="",
            dependencies=[],
            notes="",
        )
        result = render_amd(spec)
        assert "## Data Models" not in result
        assert "## Flow" not in result
        assert "## Dependencies" not in result
        assert "## Notes" in result

    def test_save_amd_creates_file(self, temp_dir: Path):
        spec = AMDSpec(
            title="Save Test",
            spec_id="SMD-003",
            status=Status.DRAFT,
            overview="Overview.",
            components=[
                Component(
                    name="Comp",
                    path="src/c.py",
                    description="Desc.",
                    interface="def f(): ...",
                ),
            ],
        )
        out = temp_dir / "output.amd.md"
        result_path = save_amd(spec, out)
        assert result_path == out
        assert out.exists()
        assert out.read_text(encoding="utf-8") == render_amd(spec)

    def test_save_amd_creates_parent_dirs(self, temp_dir: Path):
        spec = AMDSpec(
            title="Nested",
            spec_id="SMD-004",
            status=Status.DRAFT,
            overview="Overview.",
            components=[
                Component(
                    name="Comp",
                    path="src/c.py",
                    description="Desc.",
                    interface="def f(): ...",
                ),
            ],
        )
        out = temp_dir / "a" / "b" / "c" / "spec.amd.md"
        save_amd(spec, out)
        assert out.exists()

    def test_save_amd_raises_if_exists(self, temp_dir: Path):
        spec = AMDSpec(
            title="Exists",
            spec_id="SMD-005",
            status=Status.DRAFT,
            overview="Overview.",
            components=[
                Component(
                    name="Comp",
                    path="src/c.py",
                    description="Desc.",
                    interface="def f(): ...",
                ),
            ],
        )
        out = temp_dir / "exists.amd.md"
        out.write_text("existing content", encoding="utf-8")
        with pytest.raises(FileExistsError):
            save_amd(spec, out)

    def test_save_amd_overwrites_when_flag_set(self, temp_dir: Path):
        spec = AMDSpec(
            title="Overwrite",
            spec_id="SMD-006",
            status=Status.DRAFT,
            overview="Overview.",
            components=[
                Component(
                    name="Comp",
                    path="src/c.py",
                    description="Desc.",
                    interface="def f(): ...",
                ),
            ],
        )
        out = temp_dir / "overwrite.amd.md"
        out.write_text("old content", encoding="utf-8")
        save_amd(spec, out, overwrite=True)
        assert out.read_text(encoding="utf-8") == render_amd(spec)
