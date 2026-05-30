from ossature.promptspec.profile import LanguageProfile, register_profile

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines core types for an auth module in a library crate. For a binary crate the path would be `src/auth/types.rs` rooted under `src/main.rs` instead:

title: "Auth: Data Types & Errors"
description: "Define the core types, token structs, and error enum for the auth module."
outputs: ["src/auth/types.rs"]
depends_on: [1]
spec_refs: ["overview", "Requirements > Token Format"]
arch_refs: ["data models", "Components > TokenManager"]
verify: ["cargo check"]
context_files: []
</example>
<example>
A scaffold-only task that emits Cargo.toml before any source exists. The verify must not invoke `cargo build` because no source exists yet:

title: "Scaffold Cargo.toml"
description: "Create the project's Cargo.toml with package metadata."
outputs: ["Cargo.toml"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["test -f Cargo.toml"]
context_files: []
</example>
<example>
A binary-crate entrypoint that compiles and exercises the produced binary. The Cargo.toml scaffold above is a dependency, so `cargo build` is safe to invoke:

title: "auth: CLI entrypoint"
description: "Implement the main entrypoint and basic --help/--version handling."
outputs: ["src/main.rs"]
depends_on: [2]
spec_refs: ["overview", "Requirements > CLI"]
arch_refs: []
verify: ["cargo build --release", "./target/release/auth --help > /dev/null", "./target/release/auth --version > /dev/null"]
context_files: []
</example>
<example>
An integration test that exercises the types from earlier tasks. `cargo test` is fine here because all referenced source is already in place:

title: "Auth: Type Tests"
description: "Integration tests covering the type and error definitions from the auth types module."
outputs: ["tests/auth_types.rs"]
depends_on: [1, 2]
spec_refs: ["Requirements > Token Format"]
arch_refs: []
verify: ["cargo test --test auth_types"]
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
    name="rust",
    setup_command_example="`cargo init`",
    setup_manifest_example="`Cargo.toml`",
    scaffold_manifests="`Cargo.toml`, `build.rs`, `rust-toolchain.toml`",
    build_invocation_examples="`cargo build`, `cargo run`, `cargo test`, `cargo install`",
    safe_verify_examples=(
        "`cargo check` once at least one source file exists, "
        "`cargo clippy --no-deps` for a richer lint when the project allows it, "
        "`cargo metadata --no-deps --manifest-path Cargo.toml --format-version 1 > /dev/null` "
        "for manifest-only validation, `cargo fmt --check` for format-only validation, "
        "or `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="cargo check",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
