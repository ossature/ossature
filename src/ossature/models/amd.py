from dataclasses import dataclass, field

from ossature.models.shared import Status


@dataclass
class Component:
    name: str
    path: str
    description: str
    interface: str
    interface_language: str = ""
    # Behavioral contracts the implementation must uphold: preconditions,
    # postconditions, and invariants the interface signature alone can't
    # express. Optional; empty when the component has no declared contracts.
    contracts: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class DataModel:
    name: str
    definition: str
    definition_language: str = ""


@dataclass
class Dependency:
    name: str
    purpose: str


@dataclass
class AMDSpec:
    title: str
    spec_id: str
    status: Status
    overview: str
    components: list[Component] = field(default_factory=list)
    data_models: list[DataModel] = field(default_factory=list)
    flow: str = ""
    dependencies: list[Dependency] = field(default_factory=list)
    notes: str = ""
    # Non-fatal parse diagnostics (unknown sections and the like). These are
    # populated by the parser, never rendered, and excluded from equality so
    # round-trips compare on spec content only.
    warnings: list[str] = field(default_factory=list, compare=False)
