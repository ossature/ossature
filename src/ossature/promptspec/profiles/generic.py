from ossature.promptspec.profile import LanguageProfile, register_generic

# The generic profile is the fallback for any language without a curated
# profile. The wording here is directive: it tells the planner to read
# the project's manifest first and pick verify commands that work on a
# single freshly written file. Curated profiles are sharper because the
# tooling for a known language is fixed; here it has to be inferred.

_WORKED_EXAMPLES = """\
<example>
A first task that defines the core types for an auth module. The exact file extension, the directory layout, and the verify command come from the ${language} ecosystem; inspect the project's manifest if present:

title: "Auth: Data Types & Errors"
description: "Define the core types and error type for the auth module. These types are used by all subsequent auth components."
outputs: ["src/auth/types.<the standard source extension for ${language}>"]
depends_on: [1]
spec_refs: ["overview", "Requirements > Token Format"]
arch_refs: ["data models", "Components > TokenManager"]
verify: ["<a single-file syntax check appropriate for ${language}, or `test -f` if no such check exists>"]
context_files: []
</example>
<example>
A scaffold-only task that emits the project's primary build configuration before any source exists. The verify must not invoke the build because the source the build references is produced by a later task:

title: "Scaffold build configuration"
description: "Create the project's primary build configuration file for the ${language} toolchain."
outputs: ["<the manifest the ${language} toolchain expects>"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["test -f <the manifest you wrote>"]
context_files: []
</example>
<example>
A copy-only task that bundles pre-mastered assets from the context directory. No LLM call, no verify. Source and output patterns pair 1:1 and share the same `*` slot, so each matched basename is preserved in the output path:

title: "Copy SFX"
description: "Bundle the pre-mastered audio files into the assets directory."
outputs: ["src/assets/*.mp3"]
depends_on: []
spec_refs: ["Audio Assets"]
arch_refs: []
verify: []
context_files: []
source: ["context://assets/audio/*.mp3"]
</example>"""

PROFILE = LanguageProfile(
    name="__generic__",
    setup_command_example=(
        "the bootstrap command for ${language} (look at the project's manifest or spec for a hint)"
    ),
    setup_manifest_example="the manifest file the bootstrap command produces",
    scaffold_manifests=(
        "whatever build configuration and manifest files ${language} expects "
        "(read the project's spec or context to find out)"
    ),
    build_invocation_examples=(
        "the install, compile, or run commands for ${language}, basically "
        "anything that touches source files produced by later tasks"
    ),
    safe_verify_examples=(
        "a single-file syntax check using the ${language} compiler or linter "
        "if the project has one configured, a manifest-validity check that "
        "reads the file you just wrote, or `test -f <path>` when no richer "
        "check is safe"
    ),
    common_verify_command=(
        "the standard single-file syntax check for ${language}, often the "
        "compiler with a `--check` or `--no-emit` flag, or the project's linter"
    ),
    worked_examples=_WORKED_EXAMPLES,
)

register_generic(PROFILE)
