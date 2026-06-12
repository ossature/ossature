from pathlib import Path

from conftest import make_config, make_smd

from ossature.build.builder import (
    _render_arch_ref,
    _render_spec_ref,
    assemble_task_prompt,
    components_for_paths,
)
from ossature.config.loader import OssatureConfig, OutputConfig
from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.plan import PlanTask
from ossature.models.shared import Status
from ossature.models.smd import Example, Priority, Requirement, SMDSpec


def _make_task(
    spec: str = "AUTH",
    spec_refs: list[str] | None = None,
    arch_refs: list[str] | None = None,
    inject_files: list[str] | None = None,
    cross_spec_interfaces: list[str] | None = None,
    context_files: list[str] | None = None,
    notes: str = "",
    outputs: list[str] | None = None,
) -> PlanTask:
    return PlanTask(
        id="001",
        spec=spec,
        title="Test task",
        description="Build something",
        outputs=outputs or [],
        depends_on=[],
        spec_refs=spec_refs or [],
        arch_refs=arch_refs or [],
        verify="",
        inject_files=inject_files or [],
        cross_spec_interfaces=cross_spec_interfaces or [],
        context_files=context_files or [],
        notes=notes,
    )


def _full_smd() -> SMDSpec:
    return SMDSpec(
        title="Auth Module",
        spec_id="AUTH",
        status=Status.DRAFT,
        priority=Priority.HIGH,
        overview="Auth handles login.",
        goals=["Secure login", "JWT support"],
        non_goals=["OAuth"],
        constraints=["No external deps"],
        acceptance_criteria=["Tokens expire"],
        notes="Some notes here.",
        requirements=[
            Requirement(
                title="Login",
                description="User logs in.",
                accepts="email, password",
                returns="token",
            ),
        ],
        examples=[
            Example(name="Valid login", input="user/pass", output="token123"),
        ],
    )


def _full_amd() -> AMDSpec:
    return AMDSpec(
        title="Auth Architecture",
        spec_id="AUTH",
        status=Status.DRAFT,
        overview="Layered auth design.",
        components=[
            Component(
                name="TokenService",
                path="src/auth/token.rs",
                description="Issues tokens.",
                interface="fn issue() -> Token",
                contracts=["Issued tokens expire after 24h"],
            ),
        ],
        data_models=[
            DataModel(name="Token", definition="struct Token { id: String }"),
        ],
        flow="login -> issue token",
        dependencies=[Dependency(name="jwt", purpose="signing")],
        notes="Arch notes.",
    )


class TestRenderSpecRef:
    def test_overview(self):
        smd = _full_smd()
        assert _render_spec_ref(smd, "overview") == "### Overview\n\nAuth handles login."

    def test_goals(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "goals")
        assert result == "### Goals\n\n- Secure login\n- JWT support"

    def test_goals_empty_returns_none(self):
        smd = _full_smd()
        smd.goals = []
        assert _render_spec_ref(smd, "goals") is None

    def test_non_goals(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "non-goals")
        assert result == "### Non-Goals\n\n- OAuth"

    def test_non_goals_empty_returns_none(self):
        smd = _full_smd()
        smd.non_goals = []
        assert _render_spec_ref(smd, "non-goals") is None

    def test_constraints(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "constraints")
        assert result == "### Constraints\n\n- No external deps"

    def test_constraints_empty_returns_none(self):
        smd = _full_smd()
        smd.constraints = []
        assert _render_spec_ref(smd, "constraints") is None

    def test_acceptance_criteria(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "acceptance criteria")
        assert result == "### Acceptance Criteria\n\n- Tokens expire"

    def test_acceptance_criteria_empty_returns_none(self):
        smd = _full_smd()
        smd.acceptance_criteria = []
        assert _render_spec_ref(smd, "acceptance criteria") is None

    def test_notes(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "notes")
        assert result == "### Notes\n\nSome notes here."

    def test_notes_empty_returns_none(self):
        smd = _full_smd()
        smd.notes = ""
        assert _render_spec_ref(smd, "notes") is None

    def test_requirements(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "requirements")
        assert result is not None
        assert result.startswith("## Requirements\n\n")
        assert "### Login" in result

    def test_requirements_empty_returns_none(self):
        smd = _full_smd()
        smd.requirements = []
        assert _render_spec_ref(smd, "requirements") is None

    def test_examples(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "examples")
        assert result is not None
        assert result.startswith("## Examples\n\n")
        assert "### Valid login" in result

    def test_examples_empty_returns_none(self):
        smd = _full_smd()
        smd.examples = []
        assert _render_spec_ref(smd, "examples") is None

    def test_individual_requirement_by_title(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "login")
        assert result is not None
        assert result.startswith("### Login")
        assert "**Accepts:** email, password" in result

    def test_individual_example_by_name(self):
        smd = _full_smd()
        result = _render_spec_ref(smd, "valid login")
        assert result is not None
        assert result.startswith("### Valid login")

    def test_unknown_section_returns_none(self):
        smd = _full_smd()
        assert _render_spec_ref(smd, "nope") is None


class TestRenderArchRef:
    def test_overview(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "overview")
        assert result == "### Overview\n\nLayered auth design."

    def test_overview_empty_returns_none(self):
        amd = _full_amd()
        amd.overview = ""
        assert _render_arch_ref([amd], "overview") is None

    def test_dependencies(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "dependencies")
        assert result is not None
        assert result.startswith("### Dependencies\n\n")
        assert "jwt" in result

    def test_dependencies_empty_returns_none(self):
        amd = _full_amd()
        amd.dependencies = []
        assert _render_arch_ref([amd], "dependencies") is None

    def test_flow(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "flow")
        assert result == "### Flow\n\n```\nlogin -> issue token\n```"

    def test_flow_empty_returns_none(self):
        amd = _full_amd()
        amd.flow = ""
        assert _render_arch_ref([amd], "flow") is None

    def test_notes(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "notes")
        assert result == "### Notes\n\nArch notes."

    def test_notes_empty_returns_none(self):
        amd = _full_amd()
        amd.notes = ""
        assert _render_arch_ref([amd], "notes") is None

    def test_component_by_name(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "components > TokenService")
        assert result is not None
        assert "TokenService" in result
        assert "src/auth/token.rs" in result

    def test_component_unknown_returns_none(self):
        amd = _full_amd()
        assert _render_arch_ref([amd], "components > Missing") is None

    def test_component_includes_contracts(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "components > TokenService")
        assert result is not None
        assert "**Contracts:**" in result
        assert "- Issued tokens expire after 24h" in result

    def test_data_model_by_name(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "data models > Token")
        assert result is not None
        assert "Token" in result

    def test_data_model_unknown_returns_none(self):
        amd = _full_amd()
        assert _render_arch_ref([amd], "data models > Missing") is None

    def test_bare_components_renders_all(self):
        # The planner worked examples use bare section refs, so they must
        # render instead of being silently dropped.
        amd = _full_amd()
        result = _render_arch_ref([amd], "components")
        assert result is not None
        assert "### TokenService" in result
        assert "Issued tokens expire after 24h" in result

    def test_bare_data_models_renders_all(self):
        amd = _full_amd()
        result = _render_arch_ref([amd], "data models")
        assert result is not None
        assert "### Token" in result
        assert "struct Token { id: String }" in result

    def test_unknown_section_returns_none(self):
        amd = _full_amd()
        assert _render_arch_ref([amd], "nope") is None


class TestComponentsForPaths:
    def test_matches_component_by_output_path(self):
        amd = _full_amd()
        comps = components_for_paths([amd], ["src/auth/token.rs"])
        assert [c.name for c in comps] == ["TokenService"]

    def test_match_is_case_insensitive(self):
        amd = _full_amd()
        comps = components_for_paths([amd], ["SRC/Auth/Token.RS"])
        assert [c.name for c in comps] == ["TokenService"]

    def test_match_normalizes_dot_prefix(self):
        # Hand-written @path values may carry a './' prefix; the comparison
        # normalizes both sides so ownership is not silently missed.
        amd = _full_amd()
        assert [c.name for c in components_for_paths([amd], ["./src/auth/token.rs"])] == [
            "TokenService"
        ]

        amd.components[0].path = "./src/auth/token.rs"
        assert [c.name for c in components_for_paths([amd], ["src/auth/token.rs"])] == [
            "TokenService"
        ]

    def test_no_match_returns_empty(self):
        amd = _full_amd()
        assert components_for_paths([amd], ["src/other.rs"]) == []
        assert components_for_paths([amd], []) == []


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
        task = _make_task(arch_refs=["flow"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert "<architecture_context>" not in prompt

    def test_arch_refs_includes_component_contracts(self, temp_dir: Path):
        config = make_config(temp_dir)
        amd = _full_amd()
        task = _make_task(arch_refs=["components > TokenService"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert "<architecture_context>" in prompt
        assert "**Contracts:**" in prompt
        assert "Issued tokens expire after 24h" in prompt

    def test_owned_component_included_without_arch_ref(self, temp_dir: Path):
        # The component whose @path matches a task output is included even
        # when the planner left it out of arch_refs, so contracts reach the
        # implementer deterministically.
        config = make_config(temp_dir)
        amd = _full_amd()
        task = _make_task(outputs=["src/auth/token.rs"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert "<architecture_context>" in prompt
        assert "### TokenService" in prompt
        assert "Issued tokens expire after 24h" in prompt

    def test_owned_component_not_duplicated_when_in_arch_refs(self, temp_dir: Path):
        config = make_config(temp_dir)
        amd = _full_amd()
        task = _make_task(
            arch_refs=["components > TokenService"],
            outputs=["src/auth/token.rs"],
        )

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert prompt.count("### TokenService") == 1

    def test_owned_component_not_duplicated_when_in_bare_components_ref(self, temp_dir: Path):
        config = make_config(temp_dir)
        amd = _full_amd()
        task = _make_task(arch_refs=["components"], outputs=["src/auth/token.rs"])

        prompt = assemble_task_prompt(task, config, {}, {"AUTH": [amd]})

        assert prompt.count("### TokenService") == 1


class TestAssembleTaskPromptSections:
    def test_framework_line_when_set(self, temp_dir: Path):
        config = OssatureConfig(
            name="test",
            version="0.0.1",
            root=temp_dir,
            output=OutputConfig(language="python", framework="django"),
        )
        task = _make_task()

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "Framework: django" in prompt

    def test_project_brief_included_when_present(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.metadata_context_path.mkdir(parents=True, exist_ok=True)
        (config.metadata_context_path / "project-brief.md").write_text("Brief body.")
        task = _make_task()

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<project_brief>" in prompt
        assert "Brief body." in prompt

    def test_spec_brief_included_when_present(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.metadata_context_spec_briefs_path.mkdir(parents=True, exist_ok=True)
        (config.metadata_context_spec_briefs_path / "AUTH.md").write_text("Auth brief.")
        task = _make_task(spec="AUTH")

        prompt = assemble_task_prompt(task, config, {}, {})

        assert '<spec_brief spec="AUTH">' in prompt
        assert "Auth brief." in prompt

    def test_inject_files_lists_existing_files_only(self, temp_dir: Path):
        config = make_config(temp_dir)
        (config.output_path / "src").mkdir(parents=True, exist_ok=True)
        (config.output_path / "src" / "exists.rs").write_text("// here")
        task = _make_task(inject_files=["src/exists.rs", "src/missing.rs"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<dependency_files>" in prompt
        assert "`src/exists.rs`" in prompt
        assert "`src/missing.rs`" not in prompt

    def test_inject_files_omitted_when_none_exist(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = _make_task(inject_files=["src/missing.rs"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<dependency_files>" not in prompt

    def test_cross_spec_interfaces_included_when_present(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.metadata_context_interfaces_path.mkdir(parents=True, exist_ok=True)
        (config.metadata_context_interfaces_path / "DB.md").write_text("DB iface.")
        task = _make_task(cross_spec_interfaces=["DB"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<cross_spec_interfaces>" in prompt
        assert '<interface spec="DB">' in prompt
        assert "DB iface." in prompt

    def test_cross_spec_interfaces_missing_file_omits_section(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = _make_task(cross_spec_interfaces=["DB"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<cross_spec_interfaces>" not in prompt

    def test_context_files_text_file_inlined(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.context_path.mkdir(parents=True, exist_ok=True)
        (config.context_path / "spec.md").write_text("hello world")
        task = _make_task(context_files=["spec.md"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<context_files>" in prompt
        assert "### spec.md" in prompt
        assert "hello world" in prompt

    def test_context_files_missing_marked_not_found(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.context_path.mkdir(parents=True, exist_ok=True)
        task = _make_task(context_files=["gone.md"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<context_files>" in prompt
        assert "`gone.md` — not found" in prompt

    def test_context_files_binary_lists_metadata_only(self, temp_dir: Path):
        config = make_config(temp_dir)
        config.context_path.mkdir(parents=True, exist_ok=True)
        (config.context_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        task = _make_task(context_files=["logo.png"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "<context_files>" in prompt
        assert "`logo.png`" in prompt
        assert "image/png" in prompt
        assert "```" not in prompt.split("<context_files>")[1].split("</context_files>")[0]

    def test_task_block_includes_notes_and_outputs(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = _make_task(notes="Be careful.", outputs=["src/main.rs", "tests/main.rs"])

        prompt = assemble_task_prompt(task, config, {}, {})

        assert "**Notes:** Be careful." in prompt
        assert "## Files to Produce" in prompt
        assert "- `src/main.rs`" in prompt
        assert "- `tests/main.rs`" in prompt
