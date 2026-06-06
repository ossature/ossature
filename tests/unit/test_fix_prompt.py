from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from ossature.build.builder import assemble_fix_prompt
from ossature.models.plan import PlanTask


class TestAssembleFixPrompt:
    def _make_task(self, outputs: list[str]) -> PlanTask:
        return PlanTask(
            id="010",
            spec="CORE",
            title="Test task",
            description="Fix something",
            outputs=outputs,
            depends_on=[],
            spec_refs=[],
            arch_refs=[],
            verify="cargo test",
        )

    def _make_config(self, output_dir: Path, max_inline_lines: int = 200) -> Any:
        config = MagicMock()
        config.output_path = output_dir
        config.build.max_inline_lines = max_inline_lines
        return config

    def test_small_file_inlined_fully(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        small_file = output_dir / "src" / "main.rs"
        small_file.parent.mkdir(parents=True)
        small_file.write_text("fn main() {}\n")

        task = self._make_task(["src/main.rs"])
        config = self._make_config(output_dir)

        result = assemble_fix_prompt(task, "error on line 1", config, "cargo test")
        assert "fn main() {}" in result
        assert "File is large" not in result

    def test_large_file_not_inlined(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        large_file = output_dir / "src" / "cycle.rs"
        large_file.parent.mkdir(parents=True)
        content = "\n".join(f"// line {i + 1}" for i in range(500))
        large_file.write_text(content)

        task = self._make_task(["src/cycle.rs"])
        config = self._make_config(output_dir)

        result = assemble_fix_prompt(task, "error at cycle.rs:250", config, "cargo test")
        assert "File is large" in result
        assert "read_lines" in result
        assert 'total_lines="500"' in result
        # File content should NOT be inlined
        assert "// line 250" not in result

    def test_custom_max_inline_lines(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        src_file = output_dir / "src" / "main.rs"
        src_file.parent.mkdir(parents=True)
        content = "\n".join(f"// line {i + 1}" for i in range(150))
        src_file.write_text(content)

        task = self._make_task(["src/main.rs"])

        # Default 200: 150-line file should be inlined
        config = self._make_config(output_dir, max_inline_lines=200)
        result = assemble_fix_prompt(task, "error at main.rs:75", config, "cargo test")
        assert "File is large" not in result

        # Custom 100: same file should not be inlined
        config = self._make_config(output_dir, max_inline_lines=100)
        result = assemble_fix_prompt(task, "error at main.rs:75", config, "cargo test")
        assert "File is large" in result

    def test_error_output_and_verify_included(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        task = self._make_task([])
        config = self._make_config(output_dir)

        result = assemble_fix_prompt(task, "some error", config, "cargo test")
        assert "<error_output>" in result
        assert "some error" in result
        assert "<verify_command>" in result
        assert "cargo test" in result

    def test_binary_file_skipped(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        bin_file = output_dir / "data.bin"
        bin_file.write_bytes(b"\x00\x01\x80\xff" * 100)

        task = self._make_task(["data.bin"])
        config = self._make_config(output_dir)

        result = assemble_fix_prompt(task, "error", config, "cargo test")
        assert "data.bin" not in result or "current_file" not in result

    def test_missing_file_skipped_in_prompt(self, tmp_path: Path) -> None:
        # build_task short-circuits before assemble_fix_prompt is reached
        # when expected outputs are missing, so the missing file just
        # doesn't show up here. No special handling required in the prompt.
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        present = output_dir / "src" / "main.rs"
        present.parent.mkdir(parents=True)
        present.write_text("fn main() {}\n")

        task = self._make_task(["src/main.rs", "src/lib.rs"])
        config = self._make_config(output_dir)

        result = assemble_fix_prompt(task, "missing lib.rs", config, "cargo build")
        assert "fn main() {}" in result
        # No special missing-file callout
        assert "missing_files" not in result
        assert "src/lib.rs" not in result
