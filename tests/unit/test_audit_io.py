from pathlib import Path

from conftest import make_config, make_smd

from ossature.audit.audit import (
    load_cross_spec_audit_data,
    load_spec_audit_data,
    save_audit_report,
    save_cross_spec_audit_data,
    save_spec_audit_data,
)
from ossature.audit.context import (
    compute_project_brief_input_hash,
    compute_spec_brief_input_hash,
)
from ossature.audit.manifest import create_manifest, read_manifest, write_manifest
from ossature.models.audit import (
    AuditFinding,
    CrossSpecAuditReport,
    CrossSpecFinding,
    Manifest,
    Severity,
    SpecAuditReport,
)
from ossature.models.smd import Requirement


class TestSpecAuditDataIO:
    def test_save_and_load_roundtrip(self, temp_dir: Path):
        audit_dir = temp_dir / "audits"
        report = SpecAuditReport(
            findings=[
                AuditFinding(
                    severity=Severity.ERROR,
                    location="L5",
                    issue="Missing returns section",
                    suggestion="Add a returns section",
                ),
                AuditFinding(
                    severity=Severity.WARNING,
                    location="L10",
                    issue="Vague requirement",
                    suggestion="Be more specific",
                ),
            ]
        )

        save_spec_audit_data(report, "AUTH", audit_dir)
        loaded = load_spec_audit_data("AUTH", audit_dir)

        assert loaded is not None
        assert len(loaded.findings) == 2
        assert loaded.findings[0].severity == Severity.ERROR
        assert loaded.findings[0].location == "L5"
        assert loaded.findings[0].issue == "Missing returns section"
        assert loaded.findings[1].severity == Severity.WARNING

    def test_load_nonexistent_returns_none(self, temp_dir: Path):
        audit_dir = temp_dir / "audits"
        audit_dir.mkdir()

        result = load_spec_audit_data("NONEXISTENT", audit_dir)

        assert result is None

    def test_creates_audit_dir(self, temp_dir: Path):
        audit_dir = temp_dir / "nested" / "audits"
        report = SpecAuditReport(findings=[])

        save_spec_audit_data(report, "TEST", audit_dir)

        assert audit_dir.exists()
        assert (audit_dir / "TEST" / "response.json").exists()


class TestCrossSpecAuditDataIO:
    def test_save_and_load_roundtrip(self, temp_dir: Path):
        audit_dir = temp_dir / "audits"
        report = CrossSpecAuditReport(
            findings=[
                CrossSpecFinding(
                    severity=Severity.ERROR,
                    specs=["AUTH", "USERS"],
                    issue="Inconsistent user ID type",
                    suggestion="Align on UUID",
                ),
            ]
        )

        save_cross_spec_audit_data(report, audit_dir)
        loaded = load_cross_spec_audit_data(audit_dir)

        assert loaded is not None
        assert len(loaded.findings) == 1
        assert loaded.findings[0].specs == ["AUTH", "USERS"]
        assert loaded.findings[0].issue == "Inconsistent user ID type"

    def test_load_nonexistent_returns_none(self, temp_dir: Path):
        audit_dir = temp_dir / "audits"
        audit_dir.mkdir()

        result = load_cross_spec_audit_data(audit_dir)

        assert result is None

    def test_empty_findings_roundtrip(self, temp_dir: Path):
        audit_dir = temp_dir / "audits"
        report = CrossSpecAuditReport(findings=[])

        save_cross_spec_audit_data(report, audit_dir)
        loaded = load_cross_spec_audit_data(audit_dir)

        assert loaded is not None
        assert loaded.findings == []


class TestSaveAuditReport:
    def test_writes_markdown_file(self, temp_dir: Path):
        filename = temp_dir / "report.md"
        spec_reports = {"AUTH": SpecAuditReport(findings=[])}

        save_audit_report(spec_reports, None, "test-project", filename)

        assert filename.exists()
        content = filename.read_text()
        assert content.startswith("# Audit Report:")

    def test_includes_spec_findings(self, temp_dir: Path):
        filename = temp_dir / "report.md"
        finding = AuditFinding(
            severity=Severity.ERROR,
            location="L5",
            issue="Bad requirement",
            suggestion="Fix it",
        )
        spec_reports = {"AUTH": SpecAuditReport(findings=[finding])}

        save_audit_report(spec_reports, None, "test-project", filename)

        content = filename.read_text()
        assert "ERROR" in content
        assert "Bad requirement" in content

    def test_includes_cross_spec_findings(self, temp_dir: Path):
        filename = temp_dir / "report.md"
        cross_report = CrossSpecAuditReport(
            findings=[
                CrossSpecFinding(
                    severity=Severity.WARNING,
                    specs=["AUTH", "USERS"],
                    issue="Type mismatch",
                    suggestion="Use same type",
                ),
            ]
        )

        save_audit_report({}, cross_report, "test-project", filename)

        content = filename.read_text()
        assert "Cross-Spec Findings" in content
        assert "Type mismatch" in content

    def test_no_findings_message(self, temp_dir: Path):
        filename = temp_dir / "report.md"
        spec_reports = {"AUTH": SpecAuditReport(findings=[])}

        save_audit_report(spec_reports, None, "test-project", filename)

        content = filename.read_text()
        assert "No findings" in content

    def test_creates_parent_dirs(self, temp_dir: Path):
        filename = temp_dir / "nested" / "deep" / "report.md"
        spec_reports = {"AUTH": SpecAuditReport(findings=[])}

        save_audit_report(spec_reports, None, "test-project", filename)

        assert filename.exists()


class TestManifest:
    def test_create_manifest_checksums_files(self, temp_dir: Path):
        config = make_config(temp_dir)
        spec_dir = temp_dir / "specs"
        spec_dir.mkdir()
        smd = spec_dir / "auth.smd"
        smd.write_text("some content")
        amd = spec_dir / "auth.amd"
        amd.write_text("amd content")
        (temp_dir / "ossature.toml").write_text('[llm]\nmodel = "test:x"\n')

        manifest = create_manifest(config, [smd], [amd])

        assert len(manifest.sources) > 0
        assert any("auth.smd" in key for key in manifest.sources)
        assert any("auth.amd" in key for key in manifest.sources)
        assert "ossature.toml" in manifest.sources
        for value in manifest.sources.values():
            assert value.startswith("sha256:")

    def test_write_and_read_roundtrip(self, temp_dir: Path):
        manifest = Manifest(
            sources={"./specs/auth.smd": "sha256:abc123", "ossature.toml": "sha256:def456"},
            brief_inputs={"AUTH": "sha256:111"},
            project_brief_input="sha256:222",
        )
        filepath = temp_dir / "manifest.toml"

        write_manifest(manifest, filepath)
        loaded = read_manifest(filepath)

        assert loaded is not None
        assert loaded.sources == manifest.sources
        assert loaded.brief_inputs == manifest.brief_inputs
        assert loaded.project_brief_input == manifest.project_brief_input

    def test_read_legacy_manifest_without_brief_fields(self, temp_dir: Path):
        # A manifest written before brief-input tracking must still load.
        filepath = temp_dir / "manifest.toml"
        filepath.write_text('[sources]\n"./specs/auth.smd" = "sha256:abc"\n')

        loaded = read_manifest(filepath)

        assert loaded is not None
        assert loaded.brief_inputs == {}
        assert loaded.project_brief_input == ""

    def test_read_nonexistent_returns_none(self, temp_dir: Path):
        result = read_manifest(temp_dir / "nonexistent.toml")

        assert result is None

    def test_read_invalid_toml_returns_none(self, temp_dir: Path):
        filepath = temp_dir / "bad.toml"
        filepath.write_text("{{{{not valid toml!!")

        result = read_manifest(filepath)

        assert result is None


class TestBriefInputHash:
    def test_spec_hash_unaffected_by_requirements(self, temp_dir: Path):
        config = make_config(temp_dir)
        smd = make_smd("AUTH")
        baseline = compute_spec_brief_input_hash(config, smd)

        smd.requirements.append(
            Requirement(
                title="New",
                description="An added requirement.",
                accepts="x",
                returns="y",
            )
        )

        assert compute_spec_brief_input_hash(config, smd) == baseline

    def test_spec_hash_changes_with_overview(self, temp_dir: Path):
        config = make_config(temp_dir)
        smd = make_smd("AUTH")
        baseline = compute_spec_brief_input_hash(config, smd)

        smd.overview = "A different module description."

        assert compute_spec_brief_input_hash(config, smd) != baseline

    def test_spec_hash_changes_with_depends(self, temp_dir: Path):
        config = make_config(temp_dir)
        smd = make_smd("AUTH")
        baseline = compute_spec_brief_input_hash(config, smd)

        smd.depends = ["DB"]

        assert compute_spec_brief_input_hash(config, smd) != baseline

    def test_project_hash_changes_with_framework(self, temp_dir: Path):
        config = make_config(temp_dir)
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        baseline = compute_project_brief_input_hash(config, smds)

        config.output.framework = "fastapi"

        assert compute_project_brief_input_hash(config, smds) != baseline
