from pathlib import Path
from textwrap import dedent

import pytest

from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.shared import Status
from ossature.parsers.amd import AMDParseError, parse_amd, parse_amd_file
from ossature.renderer.amd import render_amd

VALID_SPEC = dedent("""\
    ---
    spec: SMD-TEST-001
    status: draft
    ---

    # Architecture: Test System

    ## Overview

    System overview here.

    ## Components

    ### API Server

    @path: src/api/server.py

    The main API server component.

    **Interface:**

    ```python
    class APIServer:
        def start(self) -> None: ...
    ```

    **Contracts:** None

    **Depends on:** Database, Cache

    ## Data Models

    ### UserRecord

    ```sql
    CREATE TABLE users (id INT, name TEXT);
    ```

    ## Flow

    ```
    Client -> API -> DB
    ```

    ## Dependencies

    - postgresql: Primary data store
    - redis: Caching layer

    ## Notes

    Architecture notes here.
""")


class TestAMDParser:
    def test_parse_valid_spec(self):
        spec = parse_amd(VALID_SPEC)

        assert spec.title == "Architecture: Test System"
        assert spec.spec_id == "SMD-TEST-001"
        assert spec.status == Status.DRAFT
        assert spec.overview == "System overview here."

        assert len(spec.components) == 1
        comp = spec.components[0]
        assert comp.name == "API Server"
        assert comp.path == "src/api/server.py"
        assert comp.description == "The main API server component."
        assert comp.interface_language == "python"
        assert "class APIServer:" in comp.interface
        assert comp.depends_on == ["Database", "Cache"]

        assert len(spec.data_models) == 1
        dm = spec.data_models[0]
        assert dm.name == "UserRecord"
        assert dm.definition_language == "sql"
        assert "CREATE TABLE users" in dm.definition

        assert "Client -> API -> DB" in spec.flow

        assert len(spec.dependencies) == 2
        assert spec.dependencies[0].name == "postgresql"
        assert spec.dependencies[0].purpose == "Primary data store"
        assert spec.dependencies[1].name == "redis"
        assert spec.dependencies[1].purpose == "Caching layer"

        assert spec.notes == "Architecture notes here."

    def test_parse_amd_file(self, temp_dir: Path):
        spec_file = temp_dir / "test.amd.md"
        spec_file.write_text(VALID_SPEC, encoding="utf-8")

        spec = parse_amd_file(spec_file)

        assert spec.title == "Architecture: Test System"
        assert spec.spec_id == "SMD-TEST-001"
        assert spec.status == Status.DRAFT
        assert len(spec.components) == 1

    def test_missing_title(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            ## Overview

            Some overview.

            ## Components

            ### Comp

            @path: src/comp.py

            Description.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Missing H1 title" in e for e in exc_info.value.errors)

    def test_missing_metadata(self):
        text = dedent("""\
            ---
            {}
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        errors = exc_info.value.errors
        assert any("Missing required metadata: spec" in e for e in errors)
        assert any("Missing required metadata: status" in e for e in errors)

    def test_invalid_status(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: bogus
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Invalid status" in e for e in exc_info.value.errors)

    def test_missing_overview(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Components

            ### Comp

            @path: src/comp.py

            Description.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Overview" in e for e in exc_info.value.errors)

    def test_missing_components(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Missing or empty section: Components" in e for e in exc_info.value.errors)

    def test_component_missing_path(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            Description.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing @path" in e for e in exc_info.value.errors)

    def test_component_missing_description(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing description" in e for e in exc_info.value.errors)

    def test_component_missing_interface(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing **Interface:** code block" in e for e in exc_info.value.errors)

    def test_component_depends_on(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            **Depends on:** ServiceA, ServiceB
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == ["ServiceA", "ServiceB"]

    def test_component_depends_on_none(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            **Depends on:** None
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == []

    def test_component_no_depends_on_marker(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == []

    def test_component_contracts_single(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Must raise on empty input
        """)
        spec = parse_amd(text)
        assert spec.components[0].contracts == ["Must raise on empty input"]

    def test_component_contracts_multiple(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Each old must match exactly once
            - Edits are applied sequentially
            - Empty edits list raises

            **Depends on:** ServiceA
        """)
        spec = parse_amd(text)
        comp = spec.components[0]
        assert comp.contracts == [
            "Each old must match exactly once",
            "Edits are applied sequentially",
            "Empty edits list raises",
        ]
        # Contracts sit between the interface block and depends-on without
        # either marker swallowing the other.
        assert "def run(): ..." in comp.interface
        assert comp.depends_on == ["ServiceA"]

    def test_component_contracts_none(self):
        spec = parse_amd(VALID_SPEC)
        assert spec.components[0].contracts == []

    def test_component_missing_contracts_marker(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Depends on:** ServiceA
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any(
            "missing **Contracts:** (write '**Contracts:** None'" in e
            for e in exc_info.value.errors
        )

    def test_component_contracts_none_with_bullets_rejected(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            - But also a bullet
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any(
            "**Contracts:** is 'None' but also lists bullet items" in e
            for e in exc_info.value.errors
        )

    def test_component_contracts_none_with_trailing_prose_rejected(self):
        # Prose after the None line would be silently lost, so it fails
        # loudly instead.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            A note that would otherwise be dropped.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any(
            "**Contracts:** is 'None' but is followed by more content" in e
            for e in exc_info.value.errors
        )

    def test_component_contracts_present_but_empty(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            **Depends on:** ServiceA
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any(
            "**Contracts:** section needs at least one '- ' bullet item" in e
            for e in exc_info.value.errors
        )

    def test_component_contracts_before_interface(self):
        # Marker order is not fixed: contracts written before the interface
        # block must still parse, and the interface code block must not be
        # mistaken for contract content.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Contracts:**

            - Returns a sorted list

            **Interface:**

            ```python
            def run(): ...
            ```
        """)
        spec = parse_amd(text)
        comp = spec.components[0]
        assert comp.contracts == ["Returns a sorted list"]
        assert comp.interface_language == "python"
        assert "def run(): ..." in comp.interface
        assert "Returns a sorted list" not in comp.interface

    def test_marker_literal_inside_interface_code_block(self):
        # Marker text inside a fenced code block is content, not a marker;
        # the fence is excluded from marker search.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```python
            # **Contracts:** and **Depends on:** can appear in comments.
            def run() -> None: ...
            ```

            **Contracts:** None

            **Depends on:** ServiceA
        """)
        spec = parse_amd(text)
        comp = spec.components[0]
        assert "**Contracts:**" in comp.interface
        assert "def run() -> None: ..." in comp.interface
        assert comp.contracts == []
        assert comp.depends_on == ["ServiceA"]

    def test_marker_literal_mid_line_is_not_a_marker(self):
        # Markers count only at the start of a line, so prose that mentions
        # one stays in the description.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Reads the **Depends on:** line of other components.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            **Depends on:** ServiceA
        """)
        spec = parse_amd(text)
        comp = spec.components[0]
        assert comp.description == "Reads the **Depends on:** line of other components."
        assert comp.depends_on == ["ServiceA"]

    def test_contract_bullet_continuation_lines(self):
        # A wrapped bullet keeps its continuation lines, joined with spaces.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Each old must match exactly once in the file,
              otherwise the whole batch is rejected with ModelRetry
            - Empty edits list raises ModelRetry
        """)
        spec = parse_amd(text)
        assert spec.components[0].contracts == [
            "Each old must match exactly once in the file, "
            "otherwise the whole batch is rejected with ModelRetry",
            "Empty edits list raises ModelRetry",
        ]

    def test_contract_paragraph_after_blank_line_not_glued(self):
        # A blank line ends a bullet; a following paragraph is not contract
        # content.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Must raise on empty input

            A note paragraph that is not part of any bullet.
        """)
        spec = parse_amd(text)
        assert spec.components[0].contracts == ["Must raise on empty input"]

    def test_contracts_star_bullets_rejected(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            * Star bullets are not recognized
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("needs at least one '- ' bullet item" in e for e in exc_info.value.errors)

    def test_unknown_section_warning(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Custom Stuff

            Some text the parser has no field for.
        """)
        spec = parse_amd(text)
        assert spec.warnings == ["Unknown section '## Custom Stuff' is ignored"]

    def test_no_warnings_for_known_sections(self):
        spec = parse_amd(VALID_SPEC)
        assert spec.warnings == []

    def test_contracts_heading_warning_has_hint(self):
        # The form issue examples used: contracts as an H2 heading. The
        # section is unknown to the parser, and the warning points at the
        # marker form.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Contracts:

            - Misplaced contract
        """)
        spec = parse_amd(text)
        assert len(spec.warnings) == 1
        assert "Unknown section '## Contracts:'" in spec.warnings[0]
        assert "'**Contracts:**' line inside a component" in spec.warnings[0]

    def test_stray_backticks_mid_line_do_not_mask(self):
        # Backticks that do not start a line are not a fence; prose around
        # them must not be blanked out of the marker search.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Renders ``` fences in generated docs.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            **Depends on:** ServiceA
        """)
        spec = parse_amd(text)
        comp = spec.components[0]
        assert "def run(): ..." in comp.interface
        assert comp.depends_on == ["ServiceA"]

    def test_fence_with_non_word_info_string_fails_loudly(self):
        # A fence like '```python x' opens a block the interface extractor
        # cannot read; the result must be a loud missing-interface error,
        # never marker text from inside the fence parsed as real markers.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```python x
            def run(): ...
            ```

            **Contracts:**

            - a contract
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing **Interface:** code block" in e for e in exc_info.value.errors)

    def test_unterminated_fence_masks_to_end(self):
        # An unclosed fence swallows the rest of the component when markdown
        # renders it, and the parser sees it the same way.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            ```

            **Interface:**

            ```python
            def run(): ...
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing **Interface:** code block" in e for e in exc_info.value.errors)

    def test_star_bullet_after_dash_bullet_not_glued(self):
        # A '* ' line is its own (unrecognized) bullet, not a continuation
        # of the dash bullet before it.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Real dash bullet
            * Star bullet line
            - Second dash bullet
        """)
        spec = parse_amd(text)
        assert spec.components[0].contracts == [
            "Real dash bullet",
            "Second dash bullet",
        ]

    def test_fenced_example_inside_contracts_dropped(self):
        # A fenced code example under a bullet is not contract text; its
        # lines (including any '- ' lines inside) must not leak into items.
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:**

            - Output must look like:

            ```text
            - item one
            done
            ```

            - Second contract
        """)
        spec = parse_amd(text)
        assert spec.components[0].contracts == [
            "Output must look like:",
            "Second contract",
        ]

    def test_component_interface_language(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```python
            class Foo:
                pass
            ```

            **Contracts:** None
        """)
        spec = parse_amd(text)
        assert spec.components[0].interface_language == "python"

    def test_component_no_interface_language(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None
        """)
        spec = parse_amd(text)
        assert spec.components[0].interface_language == ""

    def test_component_description_ends_at_depends_marker(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description before depends.

            **Depends on:** ServiceA
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing **Interface:** code block" in e for e in exc_info.value.errors)

    def test_dependency_non_bullet_lines_ignored(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Dependencies

            Some header text
            - redis: Caching layer
        """)
        spec = parse_amd(text)
        assert len(spec.dependencies) == 1
        assert spec.dependencies[0].name == "redis"

    def test_data_model_with_language(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Data Models

            ### Users

            ```sql
            CREATE TABLE users (id INT);
            ```
        """)
        spec = parse_amd(text)
        assert spec.data_models[0].definition_language == "sql"
        assert "CREATE TABLE users" in spec.data_models[0].definition

    def test_data_model_missing_code_block(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Data Models

            ### Users

            Just some text, no code block.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing code block definition" in e for e in exc_info.value.errors)

    def test_dependency_parsing(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Dependencies

            - redis: Caching layer
        """)
        spec = parse_amd(text)
        assert len(spec.dependencies) == 1
        assert spec.dependencies[0].name == "redis"
        assert spec.dependencies[0].purpose == "Caching layer"

    def test_dependency_missing_colon(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Dependencies

            - redis no colon
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing colon separator" in e for e in exc_info.value.errors)

    def test_dependency_empty_name_or_purpose(self):
        text = dedent("""\
            ---
            spec: SMD-001
            status: draft
            ---

            # Architecture: Test

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            Description text.

            **Interface:**

            ```
            def run(): ...
            ```

            **Contracts:** None

            ## Dependencies

            - : empty purpose
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("empty name or purpose" in e for e in exc_info.value.errors)

    def test_flow_section(self):
        spec = parse_amd(VALID_SPEC)
        assert "Client -> API -> DB" in spec.flow

    def test_notes_section(self):
        spec = parse_amd(VALID_SPEC)
        assert spec.notes == "Architecture notes here."

    def test_missing_frontmatter(self):
        text = "# Architecture: Test\n\n## Overview\n\nContent.\n"
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Missing YAML frontmatter" in e for e in exc_info.value.errors)

    def test_unterminated_frontmatter(self):
        text = "---\nspec: X\n\n# Architecture: Test\n"
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Unterminated YAML frontmatter" in e for e in exc_info.value.errors)

    def test_invalid_yaml_in_frontmatter(self):
        text = "---\nspec: [unclosed\n---\n\n# Architecture: Test\n"
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Invalid YAML in frontmatter" in e for e in exc_info.value.errors)

    def test_frontmatter_not_a_mapping(self):
        text = "---\n- a\n- b\n---\n\n# Architecture: Test\n"
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Frontmatter must be a YAML mapping" in e for e in exc_info.value.errors)

    def test_round_trip(self):
        original = AMDSpec(
            title="Round Trip System",
            spec_id="SMD-RT-001",
            status=Status.DRAFT,
            overview="A round-trip test overview.",
            components=[
                Component(
                    name="Service",
                    path="src/service.py",
                    description="The main service.",
                    interface="class Service:\n    def run(self) -> None: ...",
                    interface_language="python",
                    contracts=[
                        "run() is idempotent",
                        "raises ValueError on an unconfigured service",
                    ],
                    depends_on=["Database"],
                ),
            ],
            data_models=[
                DataModel(
                    name="Record",
                    definition="CREATE TABLE records (id INT);",
                    definition_language="sql",
                ),
            ],
            flow="Client -> Service -> DB",
            dependencies=[
                Dependency(name="postgres", purpose="Primary store"),
            ],
            notes="Some notes.",
        )

        rendered = render_amd(original)
        parsed = parse_amd(rendered)

        # Title gets prefixed with "Architecture: " by the renderer
        assert parsed.title == f"Architecture: {original.title}"
        assert parsed.spec_id == original.spec_id
        assert parsed.status == original.status
        assert parsed.overview == original.overview

        assert len(parsed.components) == len(original.components)
        for orig_c, parsed_c in zip(original.components, parsed.components, strict=True):
            assert parsed_c.name == orig_c.name
            assert parsed_c.path == orig_c.path
            assert parsed_c.description == orig_c.description
            assert parsed_c.interface == orig_c.interface
            assert parsed_c.interface_language == orig_c.interface_language
            assert parsed_c.contracts == orig_c.contracts
            assert parsed_c.depends_on == orig_c.depends_on

        assert len(parsed.data_models) == len(original.data_models)
        for orig_dm, parsed_dm in zip(original.data_models, parsed.data_models, strict=True):
            assert parsed_dm.name == orig_dm.name
            assert parsed_dm.definition == orig_dm.definition
            assert parsed_dm.definition_language == orig_dm.definition_language

        assert original.flow in parsed.flow

        assert len(parsed.dependencies) == len(original.dependencies)
        for orig_dep, parsed_dep in zip(original.dependencies, parsed.dependencies, strict=True):
            assert parsed_dep.name == orig_dep.name
            assert parsed_dep.purpose == orig_dep.purpose

        assert parsed.notes == original.notes
