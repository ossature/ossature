"""The cross-spec auditor only sees what audit_cross_specs assembles into
its input, so the summary must carry the pieces its finding categories
reference: component contracts and data model definitions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import make_config, make_smd

from ossature.audit.audit import audit_cross_specs
from ossature.models.amd import AMDSpec, Component, DataModel
from ossature.models.audit import CrossSpecAuditReport
from ossature.models.shared import Status


def _capture_audit_input(temp_dir: Path, parsed_amds: list[AMDSpec]) -> str:
    config = make_config(temp_dir)
    parsed_smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]

    captured: dict[str, str] = {}

    def fake_run_agent_sync(agent, prompt, **kwargs):
        captured["input"] = prompt
        result = MagicMock()
        result.output = CrossSpecAuditReport(findings=[])
        return result

    with (
        patch("ossature.audit.audit.Agent"),
        patch("ossature.audit.audit.run_agent_sync", side_effect=fake_run_agent_sync),
    ):
        audit_cross_specs(config, parsed_smds, parsed_amds)

    return captured["input"]


class TestCrossSpecAuditInput:
    def test_component_contracts_included(self, temp_dir: Path):
        amd = AMDSpec(
            title="Auth",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="Auth.",
            components=[
                Component(
                    name="TokenService",
                    path="src/auth/tokens.py",
                    description="Issues tokens.",
                    interface="def issue() -> Token: ...",
                    contracts=["Issued tokens expire after 24h"],
                ),
            ],
        )

        audit_input = _capture_audit_input(temp_dir, [amd])

        assert "TokenService" in audit_input
        assert "Contracts:" in audit_input
        assert "- Issued tokens expire after 24h" in audit_input

    def test_data_model_definition_included(self, temp_dir: Path):
        amd = AMDSpec(
            title="Auth",
            spec_id="AUTH",
            status=Status.DRAFT,
            overview="Auth.",
            components=[
                Component(
                    name="TokenService",
                    path="src/auth/tokens.py",
                    description="Issues tokens.",
                    interface="def issue() -> Token: ...",
                ),
            ],
            data_models=[
                DataModel(name="Token", definition="class Token:\n    kind: str"),
            ],
        )

        audit_input = _capture_audit_input(temp_dir, [amd])

        assert "class Token:" in audit_input
        assert "{dm.definition}" not in audit_input
