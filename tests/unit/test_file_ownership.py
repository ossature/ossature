"""Tests for file ownership tracking in BuildContext.

Verifies that write_file/copy_context_file track to created_files,
edit_file tracks to edited_files (unless the file was created by the
same task), and output hashes only cover created_files.
"""

from pathlib import Path

from conftest import make_config

from ossature.build.state import (
    BuildState,
    TaskState,
    compute_output_hash,
    write_state,
)


class TestFileOwnershipHashing:
    """Output hash stability when files are edited by other tasks."""

    def test_output_hash_stable_when_foreign_file_edited(self, temp_dir: Path):
        """Task A owns src/lib.rs. Task B edits it. Task A's output hash
        is computed over its created_files only, so it stays stable."""
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "lib.rs").write_text("original content")
        (output_dir / "src" / "main.rs").write_text("fn main() {}")

        config = make_config(temp_dir)

        # Task A created lib.rs and main.rs
        task_a_created = ["src/lib.rs", "src/main.rs"]
        h_a = compute_output_hash(task_a_created, config)

        # Task B edits lib.rs (not in task B's created_files)
        (output_dir / "src" / "lib.rs").write_text("modified by task B")

        # Task A's hash now differs because lib.rs changed on disk
        h_a_after = compute_output_hash(task_a_created, config)
        assert h_a != h_a_after

        # The fix: task A should only track lib.rs if it created it.
        # If task B edits lib.rs, task B tracks it in edited_files,
        # and task A's created_files stays ["src/main.rs"] (task A still
        # owns lib.rs via created_files — but the key insight is task B
        # doesn't add lib.rs to ITS created_files).
        #
        # Task B's output hash covers only what task B created:
        task_b_created = ["src/utils.rs"]
        (output_dir / "src" / "utils.rs").write_text("// utils")
        h_b = compute_output_hash(task_b_created, config)

        # Editing lib.rs doesn't affect task B's hash
        (output_dir / "src" / "lib.rs").write_text("modified again")
        h_b_after = compute_output_hash(task_b_created, config)
        assert h_b == h_b_after

    def test_state_stores_created_and_edited_separately(self, temp_dir: Path):
        """State roundtrip preserves the separation between created and edited."""
        filepath = temp_dir / ".ossature" / "state.toml"
        state = BuildState()
        state.set(
            "001",
            TaskState("sha256:in1", "sha256:out1", ["src/lib.rs"], []),
        )
        state.set(
            "002",
            TaskState("sha256:in2", "sha256:out2", ["src/utils.rs"], ["src/lib.rs"]),
        )
        write_state(state, filepath)

        from ossature.build.state import load_state

        loaded = load_state(filepath)

        t1 = loaded.get("001")
        assert t1.created_files == ["src/lib.rs"]
        assert t1.edited_files == []

        t2 = loaded.get("002")
        assert t2.created_files == ["src/utils.rs"]
        assert t2.edited_files == ["src/lib.rs"]

    def test_output_hash_ignores_edited_files(self, temp_dir: Path):
        """compute_output_hash only hashes files in the created_files list,
        not the edited_files list."""
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "own.py").write_text("owned")
        (output_dir / "src" / "foreign.py").write_text("foreign v1")

        config = make_config(temp_dir)

        # Hash only covers created_files
        h1 = compute_output_hash(["src/own.py"], config)

        # Modifying a file not in created_files has no effect
        (output_dir / "src" / "foreign.py").write_text("foreign v2")
        h2 = compute_output_hash(["src/own.py"], config)
        assert h1 == h2
