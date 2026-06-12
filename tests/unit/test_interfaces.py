from conftest import make_smd

from ossature.audit.interfaces import extract_interface_from_amds, propagate_to_smd_dependents
from ossature.models.amd import AMDSpec, Component, DataModel
from ossature.models.shared import Status


class TestExtractInterfaceFromAmds:
    def test_header_contains_spec_id_and_source(self):
        amd = AMDSpec(
            title="Auth",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="Auth system.",
            components=[
                Component(
                    name="TokenManager",
                    path="src/auth/tokens.py",
                    description="JWT handling.",
                    interface="def create_token(user: User) -> str: ...",
                    interface_language="python",
                ),
            ],
        )
        result = extract_interface_from_amds("AUTH", [amd], "python")
        assert result.startswith("# Interface: AUTH")
        assert "@source: amd" in result

    def test_single_amd_with_components(self):
        amd = AMDSpec(
            title="Auth",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="Auth system.",
            components=[
                Component(
                    name="TokenManager",
                    path="src/auth/tokens.py",
                    description="JWT handling.",
                    interface="def create_token(user: User) -> str: ...",
                    interface_language="python",
                ),
                Component(
                    name="UserStore",
                    path="src/auth/users.py",
                    description="User persistence.",
                    interface="class UserStore:\n    def get(self, id: str) -> User: ...",
                    interface_language="python",
                ),
            ],
        )
        result = extract_interface_from_amds("AUTH", [amd], "python")
        assert "## Components" in result
        assert "### TokenManager" in result
        assert "### UserStore" in result
        assert "**Path:** `src/auth/tokens.py`" in result
        assert "**Path:** `src/auth/users.py`" in result
        assert "```python" in result
        assert "def create_token(user: User) -> str: ..." in result
        assert "class UserStore:" in result

    def test_single_amd_with_data_models(self):
        amd = AMDSpec(
            title="Database",
            spec_id="DB",
            status=Status.DRAFT,
            overview="Database layer.",
            data_models=[
                DataModel(
                    name="User",
                    definition="CREATE TABLE users (id INT, email TEXT);",
                    definition_language="sql",
                ),
            ],
        )
        result = extract_interface_from_amds("DB", [amd], "python")
        assert "## Data Models" in result
        assert "### User" in result
        assert "```sql" in result
        assert "CREATE TABLE users" in result

    def test_multiple_amds_merged(self):
        amd1 = AMDSpec(
            title="DB Models",
            spec_id="DB",
            status=Status.DRAFT,
            overview="Models.",
            components=[
                Component(
                    name="ModelLayer",
                    path="src/db/models.py",
                    description="ORM models.",
                    interface="class User: ...",
                    interface_language="python",
                ),
            ],
        )
        amd2 = AMDSpec(
            title="DB Migrations",
            spec_id="DB",
            status=Status.DRAFT,
            overview="Migrations.",
            components=[
                Component(
                    name="MigrationRunner",
                    path="src/db/migrate.py",
                    description="Runs migrations.",
                    interface="def migrate(version: int) -> None: ...",
                    interface_language="python",
                ),
            ],
            data_models=[
                DataModel(
                    name="Migration",
                    definition="class Migration:\n    version: int\n    sql: str",
                    definition_language="python",
                ),
            ],
        )
        result = extract_interface_from_amds("DB", [amd1, amd2], "python")
        assert "### ModelLayer" in result
        assert "### MigrationRunner" in result
        assert "### Migration" in result

    def test_interface_language_fallback(self):
        amd = AMDSpec(
            title="Auth",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="Auth.",
            components=[
                Component(
                    name="Handler",
                    path="src/handler.rs",
                    description="Request handler.",
                    interface="pub fn handle(req: Request) -> Response",
                    interface_language="",
                ),
            ],
        )
        result = extract_interface_from_amds("AUTH", [amd], "rust")
        assert "```rust" in result

    def test_component_with_depends_on(self):
        amd = AMDSpec(
            title="API",
            spec_id="API",
            status=Status.DRAFT,
            overview="API layer.",
            components=[
                Component(
                    name="Router",
                    path="src/api/router.py",
                    description="Route handling.",
                    interface="class Router: ...",
                    depends_on=["AuthMiddleware", "Database"],
                ),
            ],
        )
        result = extract_interface_from_amds("API", [amd], "python")
        assert "**Depends on:** AuthMiddleware, Database" in result

    def test_component_with_contracts(self):
        # Contracts cross the spec boundary: dependents need the declared
        # behavior, not just the signatures.
        amd = AMDSpec(
            title="Core",
            spec_id="CORE",
            status=Status.DRAFT,
            overview="Core logic.",
            components=[
                Component(
                    name="Expenses",
                    path="src/core.py",
                    description="Expense logic.",
                    interface="def delete_expense(data, expense_id): ...",
                    contracts=[
                        "delete_expense raises KeyError when no expense has the given id",
                    ],
                ),
                Component(
                    name="Helpers",
                    path="src/helpers.py",
                    description="Small helpers.",
                    interface="def fmt(x): ...",
                    contracts=[],
                ),
            ],
        )
        result = extract_interface_from_amds("CORE", [amd], "python")
        assert "**Contracts:**" in result
        assert "- delete_expense raises KeyError when no expense has the given id" in result
        # A component with no contracts adds no marker noise to the boundary.
        assert result.count("**Contracts:**") == 1

    def test_empty_components_and_data_models(self):
        amd = AMDSpec(
            title="Empty",
            spec_id="EMPTY",
            status=Status.DRAFT,
            overview="Nothing here.",
        )
        result = extract_interface_from_amds("EMPTY", [amd], "python")
        assert "# Interface: EMPTY" in result
        assert "## Components" not in result
        assert "## Data Models" not in result

    def test_data_model_language_fallback(self):
        amd = AMDSpec(
            title="DB",
            spec_id="DB",
            status=Status.DRAFT,
            overview="DB.",
            data_models=[
                DataModel(
                    name="Record",
                    definition="@dataclass\nclass Record:\n    id: int",
                    definition_language="",
                ),
            ],
        )
        result = extract_interface_from_amds("DB", [amd], "python")
        assert "```python" in result

    def test_data_model_without_definition(self):
        amd = AMDSpec(
            title="DB",
            spec_id="DB",
            status=Status.DRAFT,
            overview="DB.",
            data_models=[
                DataModel(
                    name="Placeholder",
                    definition="",
                ),
            ],
        )
        result = extract_interface_from_amds("DB", [amd], "python")
        assert "### Placeholder" in result
        assert "```" not in result.split("### Placeholder")[1]


def _make_amd(spec_id: str) -> AMDSpec:
    return AMDSpec(
        title=f"Arch {spec_id}",
        spec_id=spec_id,
        status=Status.DRAFT,
        overview=f"Architecture of {spec_id}.",
        components=[
            Component(
                name="Main",
                path=f"src/{spec_id.lower()}/main.py",
                description="Main component.",
                interface="def run(): ...",
            ),
        ],
    )


class TestPropagateToSmdDependents:
    def test_no_propagation_when_no_dependents(self):
        smds = [make_smd("AUTH")]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        result = propagate_to_smd_dependents({"AUTH"}, smds, amd_by_spec)
        assert result == {"AUTH"}

    def test_propagates_to_smd_only_dependent(self):
        smds = [
            make_smd("AUTH"),
            make_smd("API", depends=["AUTH"]),
        ]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        result = propagate_to_smd_dependents({"AUTH"}, smds, amd_by_spec)
        assert result == {"AUTH", "API"}

    def test_does_not_propagate_to_amd_backed(self):
        smds = [
            make_smd("AUTH"),
            make_smd("API", depends=["AUTH"]),
        ]
        api_amd = _make_amd("API")
        amd_by_spec = {"API": [api_amd]}
        result = propagate_to_smd_dependents({"AUTH"}, smds, amd_by_spec)
        assert result == {"AUTH"}

    def test_transitive_propagation(self):
        smds = [
            make_smd("DB"),
            make_smd("API", depends=["DB"]),
            make_smd("FRONTEND", depends=["API"]),
        ]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        result = propagate_to_smd_dependents({"DB"}, smds, amd_by_spec)
        assert result == {"DB", "API", "FRONTEND"}

    def test_transitive_stops_at_amd_backed(self):
        smds = [
            make_smd("DB"),
            make_smd("API", depends=["DB"]),
            make_smd("FRONTEND", depends=["API"]),
        ]
        api_amd = _make_amd("API")
        amd_by_spec = {"API": [api_amd]}
        result = propagate_to_smd_dependents({"DB"}, smds, amd_by_spec)
        # API has AMD so not propagated; FRONTEND depends on API (not propagated)
        # so FRONTEND also not reached
        assert result == {"DB"}

    def test_empty_changed_set(self):
        smds = [
            make_smd("AUTH"),
            make_smd("API", depends=["AUTH"]),
        ]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        result = propagate_to_smd_dependents(set(), smds, amd_by_spec)
        assert result == set()

    def test_multiple_dependencies(self):
        smds = [
            make_smd("AUTH"),
            make_smd("DB"),
            make_smd("API", depends=["AUTH", "DB"]),
        ]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        # Only AUTH changed, but API depends on AUTH → propagated
        result = propagate_to_smd_dependents({"AUTH"}, smds, amd_by_spec)
        assert result == {"AUTH", "API"}

    def test_independent_specs_no_propagation(self):
        smds = [
            make_smd("AUTH"),
            make_smd("DB"),
        ]
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        result = propagate_to_smd_dependents({"AUTH"}, smds, amd_by_spec)
        assert result == {"AUTH"}
