from pathlib import Path

from conftest import make_config, make_smd

from ossature.build.builder import assemble_task_prompt
from ossature.models.amd import AMDSpec
from ossature.models.plan import PlanTask
from ossature.models.shared import Status


def _make_task(spec_refs: list[str] | None = None, arch_refs: list[str] | None = None) -> PlanTask:
    return PlanTask(
        id="001",
        spec="AUTH",
        title="Test task",
        description="",
        outputs=[],
        depends_on=[],
        spec_refs=spec_refs or [],
        arch_refs=arch_refs or [],
        verify="",
    )


class TestAssembleTaskPromptRefs:
    def test_spec_refs_renders_specification_context(self, temp_dir: Path):
        config = make_config(temp_dir)
        smd = make_smd("AUTH")
        task = _make_task(spec_refs=["overview"])

        prompt = assemble_task_prompt(task, config, {"AUTH": smd}, {})

        assert "<specification_context>" in prompt
        assert "### Overview" in prompt
        assert "Overview of AUTH" in prompt

    def test_spec_refs_with_no_matches_omits_section(self, temp_dir: Path):
        config = make_config(temp_dir)
        smd = make_smd("AUTH")
        # "notes" has no content on the SMD, so _render_spec_ref returns None
        task = _make_task(spec_refs=["notes"])

        prompt = assemble_task_prompt(task, config, {"AUTH": smd}, {})

        assert "<specification_context>" not in prompt

    def test_arch_refs_renders_architecture_context(self, temp_dir: Path):
        config = make_config(temp_dir)
        amd = AMDSpec(
            title="AUTH Architecture",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="AUTH arch overview",
        )
        task = _make_task(arch_refs=["overview"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert "<architecture_context>" in prompt
        assert "### Overview" in prompt
        assert "AUTH arch overview" in prompt

    def test_arch_refs_with_no_matches_omits_section(self, temp_dir: Path):
        config = make_config(temp_dir)
        amd = AMDSpec(
            title="AUTH Architecture",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="AUTH arch overview",
        )
        # "flow" is empty on the AMD, so _render_arch_ref returns None
        task = _make_task(arch_refs=["flow"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert "<architecture_context>" not in prompt
