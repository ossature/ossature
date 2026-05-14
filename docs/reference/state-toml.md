# state.toml

`state.toml` records per-task input and output hashes alongside the files each task created and edited. The build loop uses it to decide whether a `done` task is still valid or needs to rebuild.

```toml
[tasks.001]
input_hash = "sha256:a1b2c3..."
output_hash = "sha256:d4e5f6..."
created_files = ["src/lib.rs", "src/main.rs"]
edited_files = ["Cargo.toml"]   # only present when non-empty
```

`created_files` determines what gets hashed. `edited_files` is just there so you can see what the task touched beyond its own files.
