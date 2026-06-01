import pytest

from ossature.promptspec import Block, PromptSpec, register, registered_ids, render
from ossature.promptspec.renderer import PromptSpecError


class TestRender:
    def test_render_joins_blocks_with_blank_line(self) -> None:
        spec = PromptSpec(
            id="test.join_blocks",
            version="1.0.0",
            blocks=(
                Block("a", "first"),
                Block("b", "second"),
                Block("c", "third"),
            ),
        )
        register(spec)
        assert render("test.join_blocks") == "first\n\nsecond\n\nthird"

    def test_render_substitutes_declared_variables(self) -> None:
        spec = PromptSpec(
            id="test.substitute_vars",
            version="1.0.0",
            variables=frozenset({"language", "tool"}),
            blocks=(
                Block("role", "Build for a ${language} project."),
                Block("body", "Use ${tool} to verify."),
            ),
        )
        register(spec)
        out = render("test.substitute_vars", language="rust", tool="cargo")
        assert out == "Build for a rust project.\n\nUse cargo to verify."

    def test_render_no_variables_returns_raw_join(self) -> None:
        spec = PromptSpec(
            id="test.no_vars",
            version="1.0.0",
            blocks=(Block("only", "literal text with ${not_a_var}"),),
        )
        register(spec)
        # When no variables are declared, Template.substitute is skipped so
        # the literal `${not_a_var}` survives untouched.
        assert render("test.no_vars") == "literal text with ${not_a_var}"

    def test_unknown_spec_id_raises(self) -> None:
        with pytest.raises(PromptSpecError, match="unknown PromptSpec id"):
            render("nope.does_not_exist")

    def test_missing_variable_raises(self) -> None:
        spec = PromptSpec(
            id="test.missing_var",
            version="1.0.0",
            variables=frozenset({"language"}),
            blocks=(Block("role", "for ${language}"),),
        )
        register(spec)
        with pytest.raises(PromptSpecError, match=r"missing variables \['language'\]"):
            render("test.missing_var")

    def test_unknown_variable_raises(self) -> None:
        spec = PromptSpec(
            id="test.unknown_var",
            version="1.0.0",
            variables=frozenset({"language"}),
            blocks=(Block("role", "for ${language}"),),
        )
        register(spec)
        with pytest.raises(PromptSpecError, match=r"unknown variables \['tone'\]"):
            render("test.unknown_var", language="rust", tone="terse")


class TestPlannerSpecs:
    """Spot checks on the plan.initial / plan.replan split."""

    def test_initial_omits_preservation_block(self) -> None:
        out = render("audit.plan_initial", language="python")
        assert "<preservation_rules>" not in out
        assert "PRESERVATION" not in out

    def test_replan_includes_preservation_block(self) -> None:
        out = render("audit.plan_replan", language="python")
        assert "<preservation_rules>" in out
        assert "PRESERVATION" in out

    def test_initial_and_replan_share_role_and_output_blocks(self) -> None:
        initial = render("audit.plan_initial", language="python")
        replan = render("audit.plan_replan", language="python")
        # Role block is identical
        role_block = "<role>\nYou are a build planner"
        assert role_block in initial
        assert role_block in replan
        # Output format opens identically
        assert "<output_format>" in initial
        assert "<output_format>" in replan


class TestRegistry:
    def test_duplicate_id_rejected(self) -> None:
        spec = PromptSpec(
            id="test.duplicate",
            version="1.0.0",
            blocks=(Block("a", "x"),),
        )
        register(spec)
        with pytest.raises(PromptSpecError, match="duplicate PromptSpec id"):
            register(spec)

    def test_shipped_specs_registered(self) -> None:
        ids = registered_ids()
        for expected_id in (
            "audit.spec_audit",
            "audit.cross_spec_audit",
            "audit.plan_initial",
            "audit.plan_replan",
            "audit.interface_inference",
            "audit.spec_fixer",
            "audit.project_brief",
            "audit.spec_brief",
            "build.implementer",
            "build.fixer",
            "build.interface_extraction",
        ):
            assert expected_id in ids
