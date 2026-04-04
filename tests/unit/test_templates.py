from pathlib import Path

from ossature.templates.manager import TemplateManager, TemplateResult


class TestTemplateManager:
    def test_init_project_creates_structure(self, temp_dir: Path):
        manager = TemplateManager(temp_dir)
        result = manager.init_project(name="test-project")

        assert result.success
        assert (temp_dir / ".gitignore").exists()
        assert (temp_dir / "ossature.toml").exists()
        assert (temp_dir / "specs").is_dir()

    def test_init_project_skips_existing_config(self, temp_dir: Path):
        (temp_dir / "ossature.toml").write_text("existing")
        manager = TemplateManager(temp_dir)
        result = manager.init_project(name="test-project")
        assert any("ossature.toml" in str(p) for p in result.skipped)
        assert (temp_dir / "ossature.toml").read_text() == "existing"

    def test_init_project_skips_existing_gitignore(self, temp_dir: Path):
        (temp_dir / ".gitignore").write_text("existing")
        manager = TemplateManager(temp_dir)
        result = manager.init_project(name="test-project")
        assert any(".gitignore" in str(p) for p in result.skipped)

    def test_init_project_custom_dirs(self, temp_dir: Path):
        manager = TemplateManager(temp_dir)
        result = manager.init_project(name="test", spec_dir="my-specs", context_dir="my-ctx")
        assert result.success
        assert (temp_dir / "my-specs").is_dir()
        assert (temp_dir / "my-ctx").is_dir()

    def test_init_project_config_contains_name(self, temp_dir: Path):
        manager = TemplateManager(temp_dir)
        manager.init_project(name="my-cool-project")
        content = (temp_dir / "ossature.toml").read_text()
        assert "my-cool-project" in content


class TestTemplateResult:
    def test_success_false_on_errors(self):
        result = TemplateResult(created=[], skipped=[], errors=["something failed"])
        assert result.success is False

    def test_success_true_no_errors(self):
        result = TemplateResult(created=[], skipped=[], errors=[])
        assert result.success is True
