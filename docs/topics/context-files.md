# Context Files

The context directory holds files that feed into the LLM's prompts during planning and building. These are files the LLM cannot produce itself: binary assets like audio samples or images that need to end up in the output, and reference material like existing code samples, API documentation, or data schemas that the LLM should read and follow.

## Why context files exist

The LLM can only work from what is in its prompt. By default, each task's prompt contains the project brief, the spec text, the task description, and interface files from dependencies. Context files extend that set with things you supply from outside the spec.

A few cases where this matters:

- A spec calls for audio processing with a specific sample file as reference data. The LLM needs to know about the file to generate the right copy logic, and the file needs to end up in the output directory.
- A spec describes an API client, and you have the provider's OpenAPI schema. Rather than pasting the whole schema into the spec, you put it in the context directory and the planner includes it in the relevant tasks' prompts.
- A spec describes a parser that should match an existing format. You have examples of valid and invalid inputs. Putting them in context gives the LLM concrete cases to work from.

## How context files flow through the pipeline

The flow has three stages.

During audit, the planner scans the context directory and builds an inventory of all files with their MIME types. This inventory goes into the planning prompt, so the planner can assign relevant context files to the tasks that need them. The planner records these assignments in each task's `context_files` field in `plan.toml`.

During build, text files, meaning anything with a text MIME type plus JSON, XML, TOML, and YAML, get inlined directly into the task prompt. The LLM reads them as part of the prompt context. Binary files (audio, images) are listed by name, MIME type, and size. The LLM uses a `copy_context_file` tool to copy them to the right location in the output directory.

## Context files and invalidation

Context file contents are included in the input hash for each task that lists them. This means replacing a file with a newer version automatically invalidates any task that uses it. The next `ossature build` will detect the hash mismatch and rebuild those tasks. See [How Invalidation Works](./invalidation.md) for the full picture.

## Configuration

The context directory defaults to `context` at the project root. To use a different path, set `context_dir` in `ossature.toml`. See [Configuration](../reference/configuration.md) for details.

For step-by-step guidance on adding context files to an existing project, see the [How-to guides](../how-to/index.md).
