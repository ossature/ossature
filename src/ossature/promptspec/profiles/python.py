from ossature.promptspec.profile import LanguageProfile, register_profile

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines core types for an auth module:

title: "Auth: Data Types & Errors"
description: "Define the core types and error enum for the auth module. These types are used by all subsequent auth components."
outputs: ["src/auth/types.py"]
depends_on: [1]
spec_refs: ["overview", "Requirements > Token Format"]
arch_refs: ["data models", "Components > TokenManager"]
verify: ["python -m py_compile src/auth/types.py"]
context_files: []
</example>
<example>
A scaffold-only task that emits a pyproject.toml before any source exists. The verify must not run the build because no source exists yet:

title: "Scaffold pyproject.toml"
description: "Create the project's pyproject.toml with build-system metadata."
outputs: ["pyproject.toml"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["python -c 'import tomllib; tomllib.loads(open(\\"pyproject.toml\\").read())'"]
context_files: []
</example>
<example>
A test task that depends on the types module above. Uses pytest, which is the universal choice for Python tests:

title: "Auth: Type Tests"
description: "Unit tests covering the type and error definitions from the auth types module."
outputs: ["tests/test_auth_types.py"]
depends_on: [1, 2]
spec_refs: ["Requirements > Token Format"]
arch_refs: []
verify: ["python -m pytest tests/test_auth_types.py -q"]
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
    name="python",
    setup_command_example="`python -m venv .venv`",
    setup_manifest_example="`pyproject.toml`",
    scaffold_manifests="`pyproject.toml`, `setup.cfg`",
    build_invocation_examples="`pip install -e .`, `pip install -r requirements.txt`, `python -m build`",
    safe_verify_examples=(
        "a parse check (`python -m py_compile <file>`), a richer lint check if the project has ruff configured "
        "(`ruff check <file>`), a toml/json validity check "
        "(`python -c 'import tomllib; tomllib.loads(open(\"pyproject.toml\").read())'`), "
        "or simply `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="python -m py_compile <files>",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
