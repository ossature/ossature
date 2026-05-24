# Importing each submodule triggers PromptSpec registration via the
# module-level `register(...)` calls inside.
from ossature.promptspec.specs.audit import (  # noqa: F401
    cross_spec_audit,
    interface_inference,
    plan_generation,
    project_brief,
    spec_audit,
    spec_brief,
    spec_fixer,
)
from ossature.promptspec.specs.build import (  # noqa: F401
    fixer,
    implementer,
    interface_extraction,
)
