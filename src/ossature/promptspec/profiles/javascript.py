from ossature.promptspec.profile import LanguageProfile, register_profile

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines core types and error classes for an auth module. The project uses ESM, signalled by `"type": "module"` in package.json:

title: "Auth: Data Types & Errors"
description: "Define the core types and error classes for the auth module. These types are used by all subsequent auth components."
outputs: ["src/auth/types.js"]
depends_on: [1]
spec_refs: ["overview", "Requirements > Token Format"]
arch_refs: ["data models", "Components > TokenManager"]
verify: ["node --check src/auth/types.js"]
context_files: []
</example>
<example>
A scaffold-only task that emits package.json before any source exists. The verify must not invoke `npm install` or `npm run build` because no source exists yet:

title: "Scaffold package.json"
description: "Create the project's package.json with module metadata, ESM type, and scripts."
outputs: ["package.json"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["node -e 'JSON.parse(require(\\"fs\\").readFileSync(\\"package.json\\"))'"]
context_files: []
</example>
<example>
A test task that depends on the types module. Uses Node 20+'s built-in test runner so no extra dependency is required:

title: "Auth: Type Tests"
description: "Unit tests covering the type and error definitions from the auth types module."
outputs: ["tests/auth-types.test.js"]
depends_on: [1, 2]
spec_refs: ["Requirements > Token Format"]
arch_refs: []
verify: ["node --test tests/auth-types.test.js"]
context_files: []
</example>
<example>
A copy-only task that bundles pre-mastered audio assets from the context directory. No LLM call, no verify. Source and output patterns pair 1:1 and share the same `*` slot, so each matched basename is preserved in the output path:

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
    name="javascript",
    setup_command_example="`npm init -y`",
    setup_manifest_example="`package.json`",
    scaffold_manifests="`package.json`",
    build_invocation_examples="`npm install`, `npm run build`, `npm run bundle`",
    safe_verify_examples=(
        "`node --check <file>` for a syntax check, "
        '`node -e \'JSON.parse(require("fs").readFileSync("package.json"))\'` '
        "for manifest validity, "
        "`node --test <file>` for a built-in test run when the file is self-contained, "
        "or `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="node --check <files>",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
