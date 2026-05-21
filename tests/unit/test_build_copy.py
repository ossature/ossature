from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomli
from conftest import make_config

import ossature.build.copy as copy_mod
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

    def test_glob_skips_directories(self, temp_dir: Path):
        """A directory whose name matches the glob must not be treated as a match."""
        _setup_project(temp_dir, {"audio/a.mp3": b"x"})
        (temp_dir / "context" / "audio" / "subdir.mp3").mkdir()
        matches = resolve_source_matches(["audio/*.mp3"], temp_dir / "context")
        assert matches == [["audio/a.mp3"]]

    def test_skips_symlink_escaping_context(self, temp_dir: Path):
        """A symlink inside the context dir that resolves to a file outside it
        must be skipped, not copied (it would leak files from outside)."""
        context = temp_dir / "context"
        context.mkdir(parents=True)
        outside = temp_dir / "outside.txt"
        outside.write_bytes(b"secret")
        (context / "link.txt").symlink_to(outside)

        matches = resolve_source_matches(["*.txt"], context)
        assert matches == [[]]


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

    def test_matched_file_not_matching_prefix_raises(self):
        with pytest.raises(CopyTaskError, match="does not fit"):
            map_sources_to_outputs(["audio/*.mp3"], [["other/foo.mp3"]], ["out/*.mp3"])

    def test_matched_file_not_matching_suffix_raises(self):
        with pytest.raises(CopyTaskError, match="does not fit"):
            map_sources_to_outputs(["audio/*.mp3"], [["audio/foo.wav"]], ["out/*.mp3"])


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

    def test_output_escaping_output_dir_fails(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.txt": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.txt"], outputs=["../escape.txt"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False
        assert not (temp_dir / "escape.txt").exists()

    def test_copy_oserror_fails_gracefully(self, temp_dir: Path):
        """If a destination's parent path is occupied by a file, mkdir raises
        OSError — the task fails cleanly instead of crashing."""
        _setup_project(temp_dir, {"a.txt": b"x"})
        (temp_dir / "output" / "dest").write_text("i am a file, not a directory")
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.txt"], outputs=["dest/a.txt"])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False

    def test_verbose_logs_copied_files(self, temp_dir: Path):
        _setup_project(temp_dir, {"a.txt": b"x"})
        config = make_config(temp_dir)
        console = MagicMock()
        task = _copy_task(source=["context://a.txt"], outputs=["a.txt"])
        result = build_copy_task(task, config, console, MagicMock(), verbose=True)
        assert result.success is True
        console.log.assert_called()

    def test_source_resolving_outside_context_fails(
        self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Defensive guard: if a matched source path escapes the context dir
        (e.g. via a symlinked path component appearing after match resolution),
        the copy aborts instead of reading outside the sandbox."""
        _setup_project(temp_dir, {"a.txt": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://a.txt"], outputs=["a.txt"])
        monkeypatch.setattr(
            copy_mod, "resolve_source_matches", lambda src, ctx: [["../outside.txt"]]
        )
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False

    def test_source_file_missing_at_copy_time_fails(
        self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Defensive guard: if a matched source file no longer exists when the
        copy loop runs (TOCTOU between match resolution and copy), the copy
        aborts cleanly."""
        _setup_project(temp_dir, {"a.txt": b"x"})
        config = make_config(temp_dir)
        task = _copy_task(source=["context://ghost.txt"], outputs=["ghost.txt"])
        monkeypatch.setattr(copy_mod, "resolve_source_matches", lambda src, ctx: [["ghost.txt"]])
        result = build_copy_task(task, config, MagicMock(), MagicMock())
        assert result.success is False
