from ossature.promptspec.profile import LanguageProfile, register_profile

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines core types and an error union for an auth module. The project uses ESM, signalled by `"type": "module"` in package.json and `"module": "ESNext"` in tsconfig.json:

title: "Auth: Data Types & Errors"
description: "Define the core types and discriminated error union for the auth module. These types are used by all subsequent auth components."
outputs: ["src/auth/types.ts"]
depends_on: [2]
spec_refs: ["overview", "Requirements > Token Format"]
arch_refs: ["data models", "Components > TokenManager"]
verify: ["npx tsc --noEmit src/auth/types.ts"]
context_files: []
</example>
<example>
A scaffold-only task that emits tsconfig.json. The verify checks the file is valid JSON; it must not invoke the compiler because there is no source to compile yet:

title: "Scaffold tsconfig.json"
description: "Create the project's tsconfig.json with strict mode, ESM target, and source path roots."
outputs: ["tsconfig.json"]
depends_on: [1]
spec_refs: ["Build System"]
arch_refs: []
verify: ["node -e 'JSON.parse(require(\\"fs\\").readFileSync(\\"tsconfig.json\\"))'"]
context_files: []
</example>
<example>
A test task that depends on the types module. Uses `tsc --noEmit` to type-check the test file. For test running, the project would typically add vitest or jest as a follow-up task once dependencies are installed:

title: "Auth: Type Tests"
description: "Type-checking tests covering the type and error definitions from the auth types module."
outputs: ["tests/auth-types.test.ts"]
depends_on: [3]
spec_refs: ["Requirements > Token Format"]
arch_refs: []
verify: ["npx tsc --noEmit tests/auth-types.test.ts"]
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
    name="typescript",
    setup_command_example="`npm init -y` followed by `npx tsc --init`",
    setup_manifest_example="`tsconfig.json`",
    scaffold_manifests="`package.json`, `tsconfig.json`",
    build_invocation_examples="`npm install`, `npm run build`, `tsc`, `tsc --build`",
    safe_verify_examples=(
        "`npx tsc --noEmit <file>` for a type check without emit, "
        '`node -e \'JSON.parse(require("fs").readFileSync("tsconfig.json"))\'` '
        "for manifest validity, "
        "or `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="npx tsc --noEmit",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
