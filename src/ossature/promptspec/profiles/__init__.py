# Importing each profile module triggers registration via the
# module-level `register_profile(...)` / `register_generic(...)` calls.
from ossature.promptspec.profiles import (  # noqa: F401
    generic,
    javascript,
    lua,
    python,
    rust,
    typescript,
    zig,
)
