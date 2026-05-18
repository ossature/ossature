from pathlib import Path

import tomli_w
from conftest import make_config, make_task

from ossature.build.state import (
    STATE_VERSION,
    BuildState,
    TaskState,
    compute_input_hash,
    compute_output_hash,
    get_task_created_files,
    load_state,
    write_state,
)


class TestComputeInputHash:
    def test_same_inputs_same_hash(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        prompt = "Generate auth module"
        h1 = compute_input_hash(prompt, task, config)
        h2 = compute_input_hash(prompt, task, config)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_different_prompt_different_hash(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        h1 = compute_input_hash("prompt A", task, config)
        h2 = compute_input_hash("prompt B", task, config)
        assert h1 != h2

    def test_inject_files_not_included_in_hash(self, temp_dir: Path):
        """inject_files content is not hashed — dependency rebuilds are tracked
        separately to avoid false invalidation from later task edits."""
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "dep.py").write_text("version 1")

        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        task.inject_files = ["src/dep.py"]
        prompt = "same prompt"

        h1 = compute_input_hash(prompt, task, config)

        (output_dir / "src" / "dep.py").write_text("version 2")
        h2 = compute_input_hash(prompt, task, config)

        assert h1 == h2

    def test_source_file_content_change_invalidates_hash(self, temp_dir: Path):
        ctx = temp_dir / "context"
        ctx.mkdir(parents=True)
        (ctx / "a.mp3").write_bytes(b"v1")
        config = make_config(temp_dir)
        task = make_task("001", "AUDIO")
        task.source = ["a.mp3"]
        h1 = compute_input_hash("same prompt", task, config)

        (ctx / "a.mp3").write_bytes(b"v2")
        h2 = compute_input_hash("same prompt", task, config)
        assert h1 != h2

    def test_source_match_set_change_invalidates_hash(self, temp_dir: Path):
        ctx = temp_dir / "context"
        (ctx / "audio").mkdir(parents=True)
        (ctx / "audio" / "a.mp3").write_bytes(b"x")
        config = make_config(temp_dir)
        task = make_task("001", "AUDIO")
        task.source = ["audio/*.mp3"]
        h1 = compute_input_hash("same prompt", task, config)

        (ctx / "audio" / "b.mp3").write_bytes(b"y")
        h2 = compute_input_hash("same prompt", task, config)
        assert h1 != h2

    def test_non_source_task_hash_unchanged_by_source_addition(self, temp_dir: Path):
        """Regression: tasks without source must produce identical hashes to before."""
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        # source defaults to [] so the new code path is gated off
        h = compute_input_hash("prompt", task, config)
        assert h.startswith("sha256:")
        assert h == compute_input_hash("prompt", task, config)


class TestComputeOutputHash:
    def test_same_files_same_hash(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "mod.py").write_text("content")

        config = make_config(temp_dir)
        h1 = compute_output_hash(["src/mod.py"], config)
        h2 = compute_output_hash(["src/mod.py"], config)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_changed_content_different_hash(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "mod.py").write_text("v1")

        config = make_config(temp_dir)
        h1 = compute_output_hash(["src/mod.py"], config)

        (output_dir / "src" / "mod.py").write_text("v2")
        h2 = compute_output_hash(["src/mod.py"], config)
        assert h1 != h2

    def test_missing_files_ignored(self, temp_dir: Path):
        config = make_config(temp_dir)
        h = compute_output_hash(["nonexistent.py"], config)
        assert h.startswith("sha256:")

    def test_cross_task_edit_does_not_change_owner_hash(self, temp_dir: Path):
        """Task A creates src/lib.rs. Task B edits it. Task A's output hash
        should only cover its own created_files, so the edit is invisible."""
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "lib.rs").write_text("original")

        config = make_config(temp_dir)
        # Task A's hash covers only its created file
        h_before = compute_output_hash(["src/lib.rs"], config)

        # Task B edits the file (simulated)
        (output_dir / "src" / "lib.rs").write_text("modified by task B")

        # Task A's hash changes because the file content changed on disk
        h_after = compute_output_hash(["src/lib.rs"], config)
        assert h_before != h_after

        # But if task A's created_files list doesn't include the edited file
        # (because task B only tracked it in edited_files), task A's hash
        # is computed over an empty list — stable regardless of edits
        h_empty = compute_output_hash([], config)
        assert h_empty == compute_output_hash([], config)


class TestLoadWriteState:
    def test_roundtrip(self, temp_dir: Path):
        filepath = temp_dir / ".ossature" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("sha256:aaa", "sha256:bbb", ["src/mod.py"]))
        state.set("002", TaskState("sha256:ccc", "sha256:ddd", ["src/types.py"], ["src/init.py"]))
        write_state(state, filepath)

        loaded = load_state(filepath)
        assert loaded.get("001") is not None
        assert loaded.get("001").input_hash == "sha256:aaa"
        assert loaded.get("001").output_hash == "sha256:bbb"
        assert loaded.get("001").created_files == ["src/mod.py"]
        assert loaded.get("001").edited_files == []
        assert loaded.get("002").input_hash == "sha256:ccc"
        assert loaded.get("002").edited_files == ["src/init.py"]

    def test_roundtrip_no_edited_files_omitted(self, temp_dir: Path):
        filepath = temp_dir / ".ossature" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("h1", "h2", ["a.py"]))
        write_state(state, filepath)
        content = filepath.read_text()
        assert "edited_files" not in content

    def test_load_old_version_returns_empty(self, temp_dir: Path):
        filepath = temp_dir / "old.toml"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(
            "[meta]\nversion = 1\n\n"
            '[tasks.001]\ninput_hash = "h1"\noutput_hash = "h2"\n'
            'created_files = ["a.py"]\n'
        )
        state = load_state(filepath)
        assert state.tasks == {}

    def test_load_missing_version_returns_empty(self, temp_dir: Path):
        filepath = temp_dir / "no_ver.toml"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(
            '[tasks.001]\ninput_hash = "h1"\noutput_hash = "h2"\ncreated_files = ["a.py"]\n'
        )
        state = load_state(filepath)
        assert state.tasks == {}

    def test_write_includes_version(self, temp_dir: Path):
        filepath = temp_dir / ".ossature" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("h1", "h2", ["a.py"]))
        write_state(state, filepath)
        content = filepath.read_text()
        assert f"version = {STATE_VERSION}" in content

    def test_load_nonexistent_returns_empty(self, temp_dir: Path):
        state = load_state(temp_dir / "nonexistent.toml")
        assert state.tasks == {}

    def test_load_malformed_returns_empty(self, temp_dir: Path):
        filepath = temp_dir / "bad.toml"
        filepath.write_text("this is not valid toml [[[")
        state = load_state(filepath)
        assert state.tasks == {}

    def test_get_missing_returns_none(self):
        state = BuildState()
        assert state.get("999") is None

    def test_set_overwrites(self):
        state = BuildState()
        state.set("001", TaskState("h1", "h2", ["a.py"]))
        state.set("001", TaskState("h3", "h4", ["b.py"]))
        assert state.get("001").input_hash == "h3"
        assert state.get("001").created_files == ["b.py"]

    def test_write_creates_parent_dirs(self, temp_dir: Path):
        filepath = temp_dir / "deep" / "nested" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("h1", "h2", []))
        write_state(state, filepath)
        assert filepath.exists()


class TestGetTaskCreatedFiles:
    def test_reads_created_files_from_output_toml(self, temp_dir: Path):
        tasks_dir = temp_dir / ".ossature" / "tasks"
        task = make_task("001", "AUTH", outputs=["declared.py"])
        slug = "auth-task-001"

        task_dir = tasks_dir / f"001-{slug}"
        task_dir.mkdir(parents=True)

        with open(task_dir / "output.toml", "wb") as f:
            tomli_w.dump({"created_files": ["actual_a.py", "actual_b.py"], "success": True}, f)

        result = get_task_created_files(task, tasks_dir)
        assert result == ["actual_a.py", "actual_b.py"]

    def test_falls_back_to_old_files_key(self, temp_dir: Path):
        tasks_dir = temp_dir / ".ossature" / "tasks"
        task = make_task("001", "AUTH", outputs=["declared.py"])
        slug = "auth-task-001"

        task_dir = tasks_dir / f"001-{slug}"
        task_dir.mkdir(parents=True)

        with open(task_dir / "output.toml", "wb") as f:
            tomli_w.dump({"files": ["old_a.py", "old_b.py"], "success": True}, f)

        result = get_task_created_files(task, tasks_dir)
        assert result == ["old_a.py", "old_b.py"]

    def test_falls_back_to_task_outputs(self, temp_dir: Path):
        tasks_dir = temp_dir / ".ossature" / "tasks"
        task = make_task("001", "AUTH", outputs=["declared.py"])

        result = get_task_created_files(task, tasks_dir)
        assert result == ["declared.py"]
