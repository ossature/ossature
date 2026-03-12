# Context Files

The context directory holds files that assist the LLM during planning and building. These are things the LLM can't generate itself: binary assets like audio or images, reference material like code samples, API docs, or data schemas.

## Setup

Set the context directory in your config (defaults to `context`):

```toml
[project]
context_dir = "context"
```

Then put your files in there:

```
context/
├── music.mp3        # binary asset, gets copied to output
├── correct.wav      # binary asset
├── schema.sql       # text reference, inlined in prompts
└── examples/
    └── auth_flow.py # text reference, readable on demand
```

## How It Works

Context files flow through three stages:

**During audit**, the planner scans the context directory and builds an inventory of all files with their MIME types. This inventory goes into the planning prompt so the planner can assign relevant context files to tasks.

**In the plan**, each task can have a `context_files` field listing which context files it needs. The planner decides this automatically.

**During build**, text files (anything with a text MIME type, plus JSON and XML) get inlined directly in the task prompt. Binary files (audio, images) are listed by name, MIME type, and size. The LLM uses a `copy_context_file` tool to copy binary assets to the right place in the output directory.

## Hashing

Context file contents are included in the input hash for each task. If you replace an audio file with a new version, any task that uses it will be automatically invalidated and rebuilt on the next `ossature build`.
