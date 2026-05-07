from pathlib import Path

from ossature.audit.audit import _read_numbered


class TestReadNumbered:
    def test_prefixes_each_line(self, temp_dir: Path):
        f = temp_dir / "test.smd"
        f.write_text("# Title\n\nid: TEST\n", encoding="utf-8")

        result = _read_numbered(f)
        lines = result.splitlines()

        assert lines[0] == "L1: # Title"
        assert lines[1] == "L2: "
        assert lines[2] == "L3: id: TEST"

    def test_sequential_numbering(self, temp_dir: Path):
        f = temp_dir / "test.smd"
        f.write_text("line one\nline two\nline three\n", encoding="utf-8")

        result = _read_numbered(f)
        lines = result.splitlines()

        for i, line in enumerate(lines, 1):
            assert line.startswith(f"L{i}: "), f"Line {i} has wrong prefix: {line!r}"

    def test_preserves_content(self, temp_dir: Path):
        original = "# My Spec\n\n## Overview\n\nSome text.\n"
        f = temp_dir / "test.smd"
        f.write_text(original, encoding="utf-8")

        result = _read_numbered(f)
        original_lines = original.splitlines()
        numbered_lines = result.splitlines()

        assert len(original_lines) == len(numbered_lines)
        for i, (orig, numbered) in enumerate(zip(original_lines, numbered_lines, strict=True), 1):
            assert numbered == f"L{i}: {orig}"

    def test_single_line_file(self, temp_dir: Path):
        f = temp_dir / "test.smd"
        f.write_text("only line", encoding="utf-8")

        result = _read_numbered(f)

        assert result == "L1: only line"

    def test_empty_file(self, temp_dir: Path):
        f = temp_dir / "test.smd"
        f.write_text("", encoding="utf-8")

        result = _read_numbered(f)

        assert result == ""
