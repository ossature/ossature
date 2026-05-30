from ossature.promptspec.profile import LanguageProfile, register_profile

# Lua projects vary, but the most common deployment shapes Ossature
# users hit are LÖVE2D games (main.lua + conf.lua + modules) and plain
# Lua scripts. This profile is written to fit both. `conf.lua` and
# `*.rockspec` cover the typical scaffold manifests; `luac -p` gives a
# fast single-file verify that doesn't require an interpreter runtime.

_WORKED_EXAMPLES = """\
<example>
A typical first task that defines the game state module. The verify uses `luac -p` for a single-file syntax check, which doesn't require running the game:

title: "Game State Module"
description: "Define the game state machine (title, playing, game-over) and the state transition table."
outputs: ["src/state.lua"]
depends_on: [1]
spec_refs: ["overview", "Game States"]
arch_refs: ["data models", "Components > StateMachine"]
verify: ["luac -p src/state.lua"]
context_files: []
</example>
<example>
A scaffold-only task that emits conf.lua for a LÖVE2D project. The verify just parse-checks the file because there is no source to run yet:

title: "Scaffold conf.lua"
description: "Create the LÖVE2D conf.lua with window size, title, and module flags."
outputs: ["conf.lua"]
depends_on: []
spec_refs: ["Build System"]
arch_refs: []
verify: ["luac -p conf.lua"]
context_files: []
</example>
<example>
A test module exercising the state machine. Tests use a small assertion helper rather than a framework so no LuaRocks dependency is required:

title: "State Machine Tests"
description: "Smoke tests covering the title -> playing -> game-over transitions."
outputs: ["tests/test_state.lua"]
depends_on: [1, 2]
spec_refs: ["Game States"]
arch_refs: []
verify: ["lua tests/test_state.lua"]
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
    name="lua",
    setup_command_example="`mkdir -p src` (Lua projects rarely need a bootstrap command)",
    setup_manifest_example="`conf.lua` (LÖVE2D) or `*.rockspec` (LuaRocks)",
    scaffold_manifests="`conf.lua`, `*.rockspec`",
    build_invocation_examples="`love .`, `luarocks make`, `luarocks install`",
    safe_verify_examples=(
        "`luac -p <file>` for a parse-only syntax check, "
        "`lua -e \"loadfile('<file>')\"` for a load check without execution, "
        "or `test -f <path>` when no richer check is safe"
    ),
    common_verify_command="luac -p <files>",
    worked_examples=_WORKED_EXAMPLES,
)

register_profile(PROFILE)
