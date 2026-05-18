# Topic Guides

Topic guides explain how Ossature works and why it works that way. They are conceptual material, not step-by-step instructions. Read them when you want to understand the system rather than accomplish a specific task.

If you are looking for step-by-step instructions, see the [Tutorials](../tutorials/index.md) or [How-to guides](../how-to/index.md). If you need precise technical details, see the [Reference](../reference/index.md).

## Available topics

**[How Validation Works](how-validation-works.md)**
Why structural validation exists, what it checks, and how SMD and AMD relate to each other.

**[How the Build Works](how-the-build-works.md)**
The build loop, the fix loop, pre-flight checks, build modes, retry, incremental re-planning, and LLM error handling.

**[How Invalidation Works](invalidation.md)**
How input and output hashes prevent unnecessary rebuilds while ensuring changed inputs always propagate to the tasks that depend on them.

**[Interfaces and Boundaries](interfaces-and-boundaries.md)**
How spec dependencies create a graph, how interface extraction scopes what downstream specs see, and how incremental re-planning works across a multi-spec project.

**[Context Files](context-files.md)**
Why the context directory exists, what kinds of files belong there, and how context files flow through the audit and build pipeline.
