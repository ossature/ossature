from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ossature.cli.wizard.amd import ask_spec_id, prompt_amd_spec
from ossature.cli.wizard.smd import prompt_smd_spec
from ossature.config.loader import ConfigError, load_config
from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.shared import Status
from ossature.models.smd import (
    Example,
    Priority,
    Requirement,
    SMDSpec,
)
from ossature.renderer.amd import save_amd
from ossature.renderer.smd import save_smd

console = Console()


def create_template_smd_spec(name: str) -> SMDSpec:
    spec_id = name.upper().replace("-", "_").replace(" ", "_")
    title = name.replace("-", " ").replace("_", " ").title()

    return SMDSpec(
        title=title,
        spec_id=spec_id,
        status=Status.DRAFT,
        priority=Priority.MEDIUM,
        overview="Brief description of what this feature does and why it exists.",
        depends=[],
        goals=[
            "Primary goal this feature achieves",
            "Secondary goal or benefit",
        ],
        non_goals=[
            "What this feature explicitly does not handle",
        ],
        requirements=[
            Requirement(
                title="Primary Action",
                description="Describe what this requirement does in plain language.",
                accepts="input_field (type), another_field (type, optional)",
                returns="Description of successful output or result",
                errors=[
                    ("Invalid input", "400 with validation error"),
                    ("Not found", "404 with error message"),
                ],
            ),
        ],
        constraints=[
            "Performance: Response time < 200ms for typical operations",
            "Security: All inputs must be validated and sanitized",
            "Compatibility: Must work with existing system components",
        ],
        examples=[
            Example(
                name="Successful Operation",
                input='{\n  "field": "value"\n}',
                output='{\n  "result": "success",\n  "data": {}\n}',
            ),
        ],
        acceptance_criteria=[
            "Primary use case works as described",
            "Error cases return appropriate responses",
            "Performance constraints are met",
        ],
        notes="Add any additional context, implementation hints, or references here.",
    )


def create_template_arch(name: str, spec_id: str) -> AMDSpec:
    title = name.replace("-", " ").replace("_", " ").title()

    return AMDSpec(
        title=title,
        spec_id=spec_id,
        status=Status.DRAFT,
        overview=(
            "High-level description of how this feature will be built "
            "and the architectural approach."
        ),
        components=[
            Component(
                name="MainHandler",
                path="src/handlers/main.py",
                description=(
                    "Entry point that handles incoming requests and coordinates the workflow."
                ),
                interface="""class MainHandler:
    def handle(self, request: Request) -> Response
    def validate(self, data: dict) -> bool""",
                interface_language="python",
                contracts=[
                    "handle returns an error Response instead of raising when validation fails",
                ],
                depends_on=["CoreService"],
            ),
            Component(
                name="CoreService",
                path="src/services/core.py",
                description="Core business logic and processing.",
                interface="""class CoreService:
    def process(self, data: dict) -> Result
    def get_by_id(self, id: str) -> Entity | None""",
                interface_language="python",
                contracts=[
                    "get_by_id returns None for unknown ids instead of raising",
                ],
                depends_on=["Repository"],
            ),
            Component(
                name="Repository",
                path="src/repositories/repo.py",
                description="Data access layer for persistence operations.",
                interface="""class Repository:
    def save(self, entity: Entity) -> Entity
    def find(self, id: str) -> Entity | None
    def delete(self, id: str) -> bool""",
                interface_language="python",
                contracts=[
                    "save returns the persisted entity with its id assigned",
                    "delete returns False when no entity has the given id",
                ],
                depends_on=[],
            ),
        ],
        data_models=[
            DataModel(
                name="Entity",
                definition="""class Entity:
    id: str
    name: str
    created_at: datetime
    updated_at: datetime""",
                definition_language="python",
            ),
            DataModel(
                name="Request",
                definition="""class Request:
    action: str
    payload: dict
    metadata: dict | None = None""",
                definition_language="python",
            ),
        ],
        flow="""Request
    -> MainHandler.validate()
    -> MainHandler.handle()
        -> CoreService.process()
            -> Repository.save()
        <- Result
    <- Response""",
        dependencies=[
            Dependency(name="pydantic", purpose="Data validation and serialization"),
            Dependency(name="sqlalchemy", purpose="Database ORM"),
        ],
        notes="Add implementation decisions, trade-offs, or technical debt notes here.",
    )


def run_new(
    name: str,
    spec_type: str,
    interactive: bool,
    config_path: Path | None,
    console: Console,
) -> None:

    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    console.print(f"\n[bold]Creating new spec:[/] {name}\n")

    if spec_type == "smd":
        if interactive:
            spec = prompt_smd_spec(name, console=console)
            if spec is None:
                raise SystemExit(0)
        else:
            spec = create_template_smd_spec(name=name)

        save_smd(spec, path=config.spec_path / f"{name}.smd")

        console.print(
            Panel(
                f"[green]✓[/green] Spec [cyan]{spec.spec_id}[/cyan] created "
                f"as [cyan]{name}.smd[/cyan] with:\n"
                f"  • {len(spec.goals)} goal(s)\n"
                f"  • {len(spec.requirements)} requirement(s)\n"
                f"  • {len(spec.constraints)} constraint(s)\n"
                f"  • {len(spec.examples)} example(s)\n"
                f"  • {len(spec.acceptance_criteria)} acceptance criterion(s)",
                title="Summary",
                border_style="green",
            )
        )

    elif spec_type == "amd":
        if interactive:
            amd_spec = prompt_amd_spec(name, spec_dir=config.spec_path, console=console)

            if amd_spec is None:
                raise SystemExit(0)
        else:
            spec_id = ask_spec_id(spec_dir=config.spec_path, console=console)
            if spec_id is None:
                raise SystemExit(0)

            amd_spec = create_template_arch(name=name, spec_id=spec_id)

        save_amd(amd_spec, path=config.spec_path / f"{name}.amd")

        console.print(
            Panel(
                f"[green]✓[/green] Architecture spec [cyan]{amd_spec.spec_id}[/cyan] created "
                f"as [cyan]{name}.amd[/cyan] with:\n"
                f"  • {len(amd_spec.components)} components(s)\n"
                f"  • {len(amd_spec.data_models)} data_models(s)\n"
                f"  • {len(amd_spec.dependencies)} dependencies(s)",
                title="Summary",
                border_style="green",
            )
        )
