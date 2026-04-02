from pathlib import Path
from textwrap import dedent

import pytest

from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.shared import Status
from ossature.parsers.amd import AMDParseError, parse_amd, parse_amd_file
from ossature.renderer.amd import render_amd

VALID_SPEC = dedent("""\
    # Architecture: Test System

    @spec: SMD-TEST-001
    @status: draft

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
            @spec: SMD-001
            @status: draft

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
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Missing H1 title" in e for e in exc_info.value.errors)

    def test_missing_metadata(self):
        text = dedent("""\
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
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        errors = exc_info.value.errors
        assert any("@spec" in e for e in errors)
        assert any("@status" in e for e in errors)

    def test_invalid_status(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: bogus

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
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Invalid @status" in e for e in exc_info.value.errors)

    def test_missing_overview(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

            ## Components

            ### Comp

            @path: src/comp.py

            Description.

            **Interface:**

            ```
            def run(): ...
            ```
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Overview" in e for e in exc_info.value.errors)

    def test_missing_components(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

            ## Overview

            Overview text.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("Missing or empty section: Components" in e for e in exc_info.value.errors)

    def test_component_missing_path(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

            ## Overview

            Overview text.

            ## Components

            ### Comp

            Description.

            **Interface:**

            ```
            def run(): ...
            ```
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing @path" in e for e in exc_info.value.errors)

    def test_component_missing_description(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

            ## Overview

            Overview text.

            ## Components

            ### Comp

            @path: src/comp.py

            **Interface:**

            ```
            def run(): ...
            ```
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing description" in e for e in exc_info.value.errors)

    def test_component_missing_interface(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            **Depends on:** ServiceA, ServiceB
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == ["ServiceA", "ServiceB"]

    def test_component_depends_on_none(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            **Depends on:** None
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == []

    def test_component_no_depends_on_marker(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
        """)
        spec = parse_amd(text)
        assert spec.components[0].depends_on == []

    def test_component_interface_language(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
        """)
        spec = parse_amd(text)
        assert spec.components[0].interface_language == "python"

    def test_component_no_interface_language(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
        """)
        spec = parse_amd(text)
        assert spec.components[0].interface_language == ""

    def test_component_description_ends_at_depends_marker(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            ## Dependencies

            Some header text
            - redis: Caching layer
        """)
        spec = parse_amd(text)
        assert len(spec.dependencies) == 1
        assert spec.dependencies[0].name == "redis"

    def test_data_model_with_language(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            ## Data Models

            ### Users

            Just some text, no code block.
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing code block definition" in e for e in exc_info.value.errors)

    def test_dependency_parsing(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            ## Dependencies

            - redis: Caching layer
        """)
        spec = parse_amd(text)
        assert len(spec.dependencies) == 1
        assert spec.dependencies[0].name == "redis"
        assert spec.dependencies[0].purpose == "Caching layer"

    def test_dependency_missing_colon(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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

            ## Dependencies

            - redis no colon
        """)
        with pytest.raises(AMDParseError) as exc_info:
            parse_amd(text)
        assert any("missing colon separator" in e for e in exc_info.value.errors)

    def test_dependency_empty_name_or_purpose(self):
        text = dedent("""\
            # Architecture: Test

            @spec: SMD-001
            @status: draft

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
