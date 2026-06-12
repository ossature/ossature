from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import make_config, make_plan, make_task

from ossature.build.builder import extract_spec_interface
from ossature.models.amd import AMDSpec, Component
from ossature.models.plan import TaskStatus
from ossature.models.shared import Status


def _amd_with_contracts() -> AMDSpec:
    return AMDSpec(
        title="Auth",
        spec_id="AUTH",
        status=Status.DRAFT,
        overview="Auth.",
        components=[
            Component(
                name="Auth",
                path="src/auth.py",
                description="Auth component.",
                interface="class Auth: ...",
                contracts=["login raises AuthError on bad credentials"],
            ),
            Component(
                name="Helpers",
                path="src/helpers.py",
                description="Helpers.",
                interface="def fmt(x): ...",
                contracts=[],
            ),
        ],
    )


class TestExtractSpecInterface:
    @patch("ossature.build.builder.Agent")
    def test_extracts_from_completed_task_outputs(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        (output_dir / "src").mkdir()
        (output_dir / "src" / "auth.py").write_text("class Auth:\n    pass\n")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "## auth.py\n\n```python\nclass Auth: ...\n```"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        console = MagicMock()
        status = MagicMock()

        extract_spec_interface("AUTH", plan, config, console, status)

        mock_agent_instance.run_sync.assert_called_once()
        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "src/auth.py" in prompt
        assert "class Auth:" in prompt

        iface_path = temp_dir / ".ossature" / "context" / "interfaces" / "AUTH.md"
        assert iface_path.exists()
        content = iface_path.read_text()
        assert "# Interface: AUTH" in content
        assert "@source: build" in content

    @patch("ossature.build.builder.Agent")
    def test_skips_non_done_tasks(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        (output_dir / "src").mkdir()
        (output_dir / "src" / "auth.py").write_text("class Auth:\n    pass\n")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth.py"], status=TaskStatus.FAILED),
            ]
        )

        console = MagicMock()
        status = MagicMock()

        extract_spec_interface("AUTH", plan, config, console, status)

        mock_agent_cls.return_value.run_sync.assert_not_called()

    @patch("ossature.build.builder.Agent")
    def test_skips_missing_output_files(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/nonexistent.py"], status=TaskStatus.DONE),
            ]
        )

        console = MagicMock()
        status = MagicMock()

        extract_spec_interface("AUTH", plan, config, console, status)

        mock_agent_cls.return_value.run_sync.assert_not_called()

    @patch("ossature.build.builder.Agent")
    def test_collects_outputs_from_multiple_tasks(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "models.py").write_text("class User: pass")
        (output_dir / "src" / "service.py").write_text("class AuthService: pass")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/models.py"], status=TaskStatus.DONE),
                make_task("002", "AUTH", outputs=["src/service.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "interface content"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        console = MagicMock()
        status = MagicMock()

        extract_spec_interface("AUTH", plan, config, console, status)

        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "src/models.py" in prompt
        assert "src/service.py" in prompt

    @patch("ossature.build.builder.Agent")
    def test_skips_copy_task_outputs(self, mock_agent_cls, temp_dir: Path):
        """Copy tasks ship verbatim assets (often binary) and have no
        generated-source interface to extract. Their outputs must be skipped
        even when they happen to be text, and binary outputs must not crash."""
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        (output_dir / "src").mkdir()
        (output_dir / "src" / "auth.py").write_text("class Auth: pass")
        (output_dir / "correct.wav").write_bytes(b"\x91\x00\xff binary audio")

        config = make_config(temp_dir)
        copy_task = make_task("001", "AUTH", outputs=["correct.wav"], status=TaskStatus.DONE)
        copy_task.source = ["correct.wav"]
        plan = make_plan(
            [
                copy_task,
                make_task("002", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "interface"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        extract_spec_interface("AUTH", plan, config, MagicMock(), MagicMock())

        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "src/auth.py" in prompt
        assert "correct.wav" not in prompt

    @patch("ossature.build.builder.Agent")
    def test_skips_binary_output_without_source_field(self, mock_agent_cls, temp_dir: Path):
        """Defensive: even a non-copy task with a binary output shouldn't crash
        interface extraction (e.g., a build.setup script that wrote a binary)."""
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        (output_dir / "src").mkdir()
        (output_dir / "src" / "auth.py").write_text("class Auth: pass")
        (output_dir / "blob.bin").write_bytes(b"\x91\x00\xff")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["blob.bin"], status=TaskStatus.DONE),
                make_task("002", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "interface"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        # Must not raise UnicodeDecodeError
        extract_spec_interface("AUTH", plan, config, MagicMock(), MagicMock())

        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "src/auth.py" in prompt
        assert "blob.bin" not in prompt

    @patch("ossature.build.builder.Agent")
    def test_appends_declared_contracts_deterministically(self, mock_agent_cls, temp_dir: Path):
        # Declared AMD contracts are merged into the build-extracted doc
        # outside the LLM call, so they survive rebuilds verbatim.
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "auth.py").write_text("class Auth: pass")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "extracted interface"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        extract_spec_interface(
            "AUTH", plan, config, MagicMock(), MagicMock(), amds=[_amd_with_contracts()]
        )

        # Contracts never enter the extraction prompt
        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "login raises AuthError" not in prompt

        content = (temp_dir / ".ossature" / "context" / "interfaces" / "AUTH.md").read_text()
        assert "## Declared Contracts" in content
        assert "### Auth" in content
        assert "- login raises AuthError on bad credentials" in content
        # Contract-free components are left out of the section
        assert "### Helpers" not in content

    @patch("ossature.build.builder.Agent")
    def test_no_contracts_section_when_amds_have_none(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "auth.py").write_text("class Auth: pass")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
            ]
        )

        amd = _amd_with_contracts()
        for comp in amd.components:
            comp.contracts = []

        mock_result = MagicMock()
        mock_result.output = "extracted interface"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        extract_spec_interface("AUTH", plan, config, MagicMock(), MagicMock(), amds=[amd])

        content = (temp_dir / ".ossature" / "context" / "interfaces" / "AUTH.md").read_text()
        assert "## Declared Contracts" not in content

    @patch("ossature.build.builder.Agent")
    def test_only_collects_from_target_spec(self, mock_agent_cls, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "auth.py").write_text("class Auth: pass")
        (output_dir / "src" / "api.py").write_text("class Api: pass")

        config = make_config(temp_dir)
        plan = make_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth.py"], status=TaskStatus.DONE),
                make_task("002", "API", outputs=["src/api.py"], status=TaskStatus.DONE),
            ]
        )

        mock_result = MagicMock()
        mock_result.output = "interface"
        mock_agent_instance = MagicMock()
        mock_agent_instance.run_sync.return_value = mock_result
        mock_agent_cls.return_value = mock_agent_instance

        console = MagicMock()
        status = MagicMock()

        extract_spec_interface("AUTH", plan, config, console, status)

        prompt = mock_agent_instance.run_sync.call_args[0][0]
        assert "src/auth.py" in prompt
        assert "src/api.py" not in prompt
