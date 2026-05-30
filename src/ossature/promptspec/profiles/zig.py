from ossature.promptspec.profile import LanguageProfile, register_profile

# Zig's modern project layout (post `zig init`) is build.zig plus
# build.zig.zon plus src/root.zig or src/main.zig. `zig ast-check`
# gives a fast single-file syntax check that doesn't require the build
# graph, which is what makes it a good safe verify for early tasks.

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines the core types for an image-quality module in a library crate. The verify uses `zig ast-check` which parses without invoking the build graph:

title: "Quality: Data Types & Errors"
description: "Define the quality score types and error set."
outputs: ["src/quality/types.zig"]
depends_on: [1]
spec_refs: ["overview", "Requirements > Quality Score"]
arch_refs: ["data models", "Components > Scorer"]
verify: ["zig ast-check src/quality/types.zig"]
context_files: []
</example>
<example>
A scaffold-only task that emits build.zig before any source exists. The verify must not invoke `zig build` because the source the build graph references is produced by a later task:

title: "Scaffold build.zig"
description: "Create the project's build.zig with module declarations and exe target."
outputs: ["build.zig"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["test -f build.zig"]
context_files: []
</example>
<example>
A main entrypoint that compiles and exercises the produced binary. build.zig is in place from the scaffold above, so `zig build` is safe to invoke:

title: "qoi: CLI entrypoint"
description: "Implement the main entrypoint and basic --help/--version handling."
outputs: ["src/main.zig"]
depends_on: [2]
spec_refs: ["overview", "Requirements > CLI"]
arch_refs: []
verify: ["zig build", "./zig-out/bin/qoi --help > /dev/null", "./zig-out/bin/qoi --version > /dev/null"]
context_files: []
</example>
<example>
A test block colocated with the quality module. Zig tests run via `zig test` on a single file or via the `test` step in build.zig:

title: "Quality: Tests"
description: "Tests for the quality score and error set."
outputs: ["src/quality/types_test.zig"]
depends_on: [1, 2]
spec_refs: ["Requirements > Quality Score"]
arch_refs: []
verify: ["zig test src/quality/types_test.zig"]
context_files: []
</example>
<example>
A copy-only task that bundles pre-mastered audio assets from the context directory. No LLM call, no verify. Source and output patterns pair 1:1 and share the same `*` slot, so each matched basename is preserved in the output path:

title: "Copy SFX"
description: "Bundle the pre-mastered audio files into the assets directory."
outputs: ["assets/audio/*.mp3"]
depends_on: []
spec_refs: ["Audio Assets"]
arch_refs: []
verify: []
context_files: []
source: ["context://assets/audio/*.mp3"]
</example>"""

PROFILE = LanguageProfile(
    name="zig",
    setup_command_example="`zig init -m`",
    setup_manifest_example="`build.zig`",
    scaffold_manifests="`build.zig`, `build.zig.zon`",
    build_invocation_examples="`zig build`, `zig build run`, `zig build test`",
    safe_verify_examples=(
        "`zig ast-check <file>` for a fast single-file syntax check, "
        "`zig fmt --check <file>` for a format-only check, "
        "or `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="zig ast-check <files>",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
