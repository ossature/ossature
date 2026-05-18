from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomli
from conftest import make_config

from ossature.build.copy import (
    CopyTaskError,
    _classify_pattern,
    assemble_copy_task_prompt,
    build_copy_task,
    map_sources_to_outputs,
    resolve_source_matches,
)
from ossature.models.plan import PlanTask, TaskStatus


def _copy_task(
    task_id: str = "001",
    title: str = "Copy assets",
    source: list[str] | None = None,
    outputs: list[str] | None = None,
) -> PlanTask:
    return PlanTask(
        id=task_id,
        spec="AUDIO",
        title=title,
        description="copy task",
        outputs=outputs or [],
        depends_on=[],
        spec_refs=[],
        arch_refs=[],
        status=TaskStatus.PENDING,
        verify=[],
        source=source or [],
    )


def _setup_project(temp_dir: Path, files: dict[str, bytes]) -> None:
    context = temp_dir / "context"
    output = temp_dir / "output"
    context.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        full = context / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)


class TestClassifyPattern:
    def test_literal_pattern_returns_none(self):
        assert _classify_pattern("assets/foo.mp3") is None

    def test_single_star(self):
        assert _classify_pattern("assets/*.mp3") == ("assets/", "*", ".mp3")

    def test_double_star(self):
        assert _classify_pattern("assets/**/foo.mp3") == ("assets/", "**", "/foo.mp3")

    def test_multiple_stars_raises(self):
        with pytest.raises(CopyTaskError):
            _classify_pattern("a/*/b/*.mp3")

    def test_mixed_star_and_double_star_raises(self):
        with pytest.raises(CopyTaskError):
            _classify_pattern("a/**/b/*.mp3")


class TestResolveSourceMatches:
    def test_literal_match(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"x"})
        matches = resolve_source_matches(["a.mp3"], temp_dir / "context")
        assert matches == [["a.mp3"]]

    def test_glob_match_sorted(self, temp_dir: Path):
        _setup_project(temp_dir, {"audio/b.mp3": b"x", "audio/a.mp3": b"y"})
        matches = resolve_source_matches(["audio/*.mp3"], temp_dir / "context")
        assert matches == [["audio/a.mp3", "audio/b.mp3"]]

    def test_recursive_glob(self, temp_dir: Path):
        _setup_project(temp_dir, {"a/b/c.mp3": b"x", "a/d.mp3": b"y"})
        matches = resolve_source_matches(["**/*.mp3"], temp_dir / "context")
        assert sorted(matches[0]) == ["a/b/c.mp3", "a/d.mp3"]

    def test_no_match_returns_empty_inner(self, temp_dir: Path):
        _setup_project(temp_dir, {})
        matches = resolve_source_matches(["*.mp3"], temp_dir / "context")
        assert matches == [[]]

    def test_missing_context_dir_returns_empty(self, temp_dir: Path):
        matches = resolve_source_matches(["*.mp3"], temp_dir / "no-such-dir")
        assert matches == [[]]

    def test_multiple_patterns(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"", "b.png": b""})
        matches = resolve_source_matches(["*.mp3", "*.png"], temp_dir / "context")
        assert matches == [["a.mp3"], ["b.png"]]


class TestMapSourcesToOutputs:
    def test_literal_one_to_one(self):
        pairs = map_sources_to_outputs(["a.json"], [["a.json"]], ["dest/a.json"])
        assert pairs == [("a.json", "dest/a.json")]

    def test_glob_basename_substitution(self):
        pairs = map_sources_to_outputs(
            ["audio/*.mp3"],
            [["audio/foo.mp3", "audio/bar.mp3"]],
            ["src/*.mp3"],
        )
        assert sorted(pairs) == [("audio/bar.mp3", "src/bar.mp3"), ("audio/foo.mp3", "src/foo.mp3")]

    def test_recursive_glob_substitution(self):
        pairs = map_sources_to_outputs(
            ["assets/**"],
            [["assets/a/b.mp3", "assets/c.mp3"]],
            ["out/**"],
        )
        assert sorted(pairs) == [("assets/a/b.mp3", "out/a/b.mp3"), ("assets/c.mp3", "out/c.mp3")]

    def test_zero_matches_raises(self):
        with pytest.raises(CopyTaskError, match="matched no files"):
            map_sources_to_outputs(["*.mp3"], [[]], ["out/*.mp3"])

    def test_count_mismatch_raises(self):
        with pytest.raises(CopyTaskError, match="entr"):
            map_sources_to_outputs(["a", "b"], [["a"], ["b"]], ["only-one"])

    def test_literal_source_with_wildcard_output_raises(self):
        with pytest.raises(CopyTaskError, match="wildcard"):
            map_sources_to_outputs(["a.mp3"], [["a.mp3"]], ["out/*.mp3"])

    def test_wildcard_source_with_literal_output_raises(self):
        with pytest.raises(CopyTaskError, match="wildcard"):
            map_sources_to_outputs(["*.mp3"], [["foo.mp3"]], ["out.mp3"])

    def test_literal_source_multiple_matches_raises(self):
        with pytest.raises(CopyTaskError, match="resolved to"):
            map_sources_to_outputs(["a.mp3"], [["a.mp3", "b.mp3"]], ["out.mp3"])

    def test_multiple_paired_patterns(self):
        pairs = map_sources_to_outputs(
            ["audio/*.mp3", "images/*.png"],
            [["audio/foo.mp3"], ["images/bar.png"]],
            ["src/*.mp3", "img/*.png"],
        )
        assert ("audio/foo.mp3", "src/foo.mp3") in pairs
        assert ("images/bar.png", "img/bar.png") in pairs


class TestAssembleCopyTaskPrompt:
    def test_includes_source_outputs_and_matches(self, temp_dir: Path):
        _setup_project(temp_dir, {"audio/a.mp3": b"x", "audio/b.mp3": b"y"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://audio/*.mp3"], outputs=["src/assets/*.mp3"])
        prompt = assemble_copy_task_prompt(task, config)
        assert "context://audio/*.mp3" in prompt
        assert "src/assets/*.mp3" in prompt
        assert "audio/a.mp3" in prompt
        assert "audio/b.mp3" in prompt

    def test_deterministic(self, temp_dir: Path):
        _setup_project(temp_dir, {"audio/a.mp3": b"x", "audio/b.mp3": b"y"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://audio/*.mp3"], outputs=["src/*.mp3"])
        assert assemble_copy_task_prompt(task, config) == assemble_copy_task_prompt(task, config)

    def test_changes_when_matches_change(self, temp_dir: Path):
        _setup_project(temp_dir, {"audio/a.mp3": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://audio/*.mp3"], outputs=["src/*.mp3"])
        before = assemble_copy_task_prompt(task, config)

        (temp_dir / "context" / "audio" / "c.mp3").write_bytes(b"z")
        after = assemble_copy_task_prompt(task, config)
        assert before != after


class TestBuildCopyTask:
    def test_single_file_copy(self, temp_dir: Path):
        _setup_project(temp_dir, {"config.json": b'{"k": 1}'})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://config.json"], outputs=["src/config.json"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is True
        assert result.created_files == ["src/config.json"]
        assert (temp_dir / "output" / "src" / "config.json").read_bytes() == b'{"k": 1}'

    def test_glob_copy_creates_all_matches(self, temp_dir: Path):
        _setup_project(temp_dir, {"audio/a.mp3": b"AAA", "audio/b.mp3": b"BBB"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://audio/*.mp3"], outputs=["src/assets/*.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is True
        assert sorted(result.created_files) == ["src/assets/a.mp3", "src/assets/b.mp3"]
        assert (temp_dir / "output" / "src" / "assets" / "a.mp3").read_bytes() == b"AAA"
        assert (temp_dir / "output" / "src" / "assets" / "b.mp3").read_bytes() == b"BBB"

    def test_zero_matches_fails(self, temp_dir: Path):
        _setup_project(temp_dir, {})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://audio/*.mp3"], outputs=["src/*.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False
        assert result.created_files == []

    def test_missing_context_dir_fails(self, temp_dir: Path):
        # No context dir created
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.mp3"], outputs=["src/a.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False

    def test_empty_source_fails(self, temp_dir: Path):
        _setup_project(temp_dir, {})
        config = make_config(temp_dir)
        task = _copy_task(source=[], outputs=["src/a.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False

    def test_creates_intermediate_directories(self, temp_dir: Path):
        _setup_project(temp_dir, {"deep/a.bin": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://deep/a.bin"], outputs=["deep/nested/dest/a.bin"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is True
        assert (temp_dir / "output" / "deep" / "nested" / "dest" / "a.bin").exists()

    def test_overwrites_existing_destination(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"NEW"})
        (temp_dir / "output" / "src").mkdir(parents=True)
        (temp_dir / "output" / "src" / "a.mp3").write_bytes(b"OLD")
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.mp3"], outputs=["src/a.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is True
        assert (temp_dir / "output" / "src" / "a.mp3").read_bytes() == b"NEW"

    def test_writes_prompt_and_response_files(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.mp3"], outputs=["src/a.mp3"])
        build_copy_task(task, config, MagicMock(), MagicMock())
        task_dir = temp_dir / ".ossature" / "tasks" / "001-copy-assets"
        assert (task_dir / "prompt.md").exists()
        assert (task_dir / "response.md").exists()
        assert (task_dir / "output.toml").exists()

    def test_output_toml_records_created_files_and_success(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.mp3"], outputs=["src/a.mp3"])
        build_copy_task(task, config, MagicMock(), MagicMock())
        out = tomli.loads(
            (temp_dir / ".ossature" / "tasks" / "001-copy-assets" / "output.toml").read_text()
        )
        assert out["success"] is True
        assert out["created_files"] == ["src/a.mp3"]

    def test_task_result_summary_includes_file_count(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.mp3": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.mp3"], outputs=["src/a.mp3"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.file_count == 1
        assert result.total_lines == 0

    def test_failed_copy_writes_output_toml_with_success_false(self, temp_dir: Path):
        _setup_project(temp_dir, {})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://nope/*.mp3"], outputs=["src/*.mp3"])
        build_copy_task(task, config, MagicMock(), MagicMock())
        out = tomli.loads(
            (temp_dir / ".ossature" / "tasks" / "001-copy-assets" / "output.toml").read_text()
        )
        assert out["success"] is False
