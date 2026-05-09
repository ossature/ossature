import os
import warnings
from pathlib import Path

import pytest

from ossature.config.loader import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    TOOL_REQUIRED_ROLES,
    ConfigError,
    LLMConfig,
    OssatureConfig,
    load_config,
)


class TestConfigLoader:
    def test_load_config_from_initialized_project(self, initialized_project: Path):

        original_cwd = os.getcwd()

        try:
            os.chdir(initialized_project)
            config = load_config()

            assert config.name == "test-project"
            assert config.root.resolve() == initialized_project.resolve()
        finally:
            os.chdir(original_cwd)

    def test_load_config_explicit_path(self, initialized_project: Path):
        config_path = initialized_project / "ossature.toml"
        config = load_config(config_path)

        assert config.name == "test-project"

    def test_load_config_not_found(self, temp_dir: Path):

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            with pytest.raises(ConfigError):
                load_config()
        finally:
            os.chdir(original_cwd)

    def test_config_paths(self, sample_config: OssatureConfig):
        assert sample_config.spec_path == sample_config.root / "specs"
        assert sample_config.context_path == sample_config.root / "context"
        assert sample_config.output_path == sample_config.root / "output"

    def test_build_config_defaults(self, sample_config: OssatureConfig):
        assert sample_config.build.max_fix_attempts == 3
        assert sample_config.build.setup == []
        assert sample_config.build.verify == []
        assert sample_config.build.test == []

    def test_load_config_with_build_section(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "rust"

[build]
setup = "cargo init"
verify = "cargo check"
test = "cargo test"
max_fix_attempts = 5

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        config = load_config(temp_dir / "ossature.toml")
        # Strings are normalized to single-element lists.
        assert config.build.setup == ["cargo init"]
        assert config.build.verify == ["cargo check"]
        assert config.build.test == ["cargo test"]
        assert config.build.max_fix_attempts == 5

    def test_load_config_with_build_section_list_form(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "c"

[build]
verify = ["gcc -o /tmp/yep yep.c", "/tmp/yep --help"]

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        config = load_config(temp_dir / "ossature.toml")
        assert config.build.verify == ["gcc -o /tmp/yep yep.c", "/tmp/yep --help"]
        assert config.build.setup == []

    def test_llm_config_defaults(self, sample_config: OssatureConfig):
        assert sample_config.llm.model == DEFAULT_MODEL
        assert sample_config.llm.audit is None
        assert sample_config.llm.build is None
        assert sample_config.llm.planner is None
        assert sample_config.llm.brief is None
        assert sample_config.llm.interface is None
        assert sample_config.llm.fixer is None
        assert sample_config.llm.ollama_base_url == DEFAULT_OLLAMA_BASE_URL
        assert sample_config.llm.retries == 3
        assert sample_config.llm.tool_retries == 5

    def test_llm_model_for_falls_back_to_default(self):
        llm = LLMConfig(model="test:default-model")
        assert llm.model_for("audit") == "test:default-model"
        assert llm.model_for("build") == "test:default-model"
        assert llm.model_for("planner") == "test:default-model"
        assert llm.model_for("brief") == "test:default-model"
        assert llm.model_for("interface") == "test:default-model"
        assert llm.model_for("fixer") == "test:default-model"

    def test_llm_model_for_uses_override(self):
        llm = LLMConfig(
            model="test:default",
            audit="test:audit-model",
            build="ollama:codellama",
        )
        assert llm.model_for("audit") == "test:audit-model"
        assert llm.model_for("build") == "ollama:codellama"
        assert llm.model_for("planner") == "test:default"
        assert llm.model_for("fixer") == "test:default"

    def test_llm_model_for_unknown_role_returns_default(self):
        llm = LLMConfig(model="test:default")
        assert llm.model_for("nonexistent") == "test:default"

    def test_load_config_with_llm_section(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
language = "rust"

[llm]
model = "ollama:deepseek-coder"
audit = "anthropic:claude-opus-4-6"
build = "anthropic:claude-sonnet-4-6"
retries = 5
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        config = load_config(temp_dir / "ossature.toml")
        assert config.llm.model == "ollama:deepseek-coder"
        assert config.llm.audit == "anthropic:claude-opus-4-6"
        assert config.llm.build == "anthropic:claude-sonnet-4-6"
        assert config.llm.planner is None
        assert config.llm.retries == 5
        assert config.llm.model_for("planner") == "ollama:deepseek-coder"
        assert config.llm.model_for("audit") == "anthropic:claude-opus-4-6"

    def test_llm_uses_ollama_default_model(self):
        llm = LLMConfig(model="ollama:deepseek-coder")
        assert llm.uses_ollama is True

    def test_llm_uses_ollama_role_override(self):
        llm = LLMConfig(model="anthropic:claude-sonnet-4-6", build="ollama:codellama")
        assert llm.uses_ollama is True

    def test_llm_uses_ollama_false(self):
        llm = LLMConfig(model="anthropic:claude-sonnet-4-6")
        assert llm.uses_ollama is False

    def test_load_config_ollama_sets_env_var(self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        config_content = """
[project]
name = "test-project"

[llm]
model = "ollama:deepseek-coder"
ollama_base_url = "http://myhost:11434"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        config = load_config(temp_dir / "ossature.toml")
        assert config.llm.ollama_base_url == "http://myhost:11434"
        assert os.environ["OLLAMA_BASE_URL"] == "http://myhost:11434"

    def test_load_config_ollama_respects_existing_env_var(
        self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://already-set:11434")
        config_content = """
[project]
name = "test-project"

[llm]
model = "ollama:deepseek-coder"
ollama_base_url = "http://from-config:11434"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        load_config(temp_dir / "ossature.toml")
        assert os.environ["OLLAMA_BASE_URL"] == "http://already-set:11434"

    def test_load_config_no_ollama_skips_env_var(
        self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        config_content = """
[project]
name = "test-project"

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        load_config(temp_dir / "ossature.toml")
        assert "OLLAMA_BASE_URL" not in os.environ

    def test_load_config_without_llm_section_raises(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
language = "python"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        with pytest.raises(ConfigError, match="Missing \\[llm\\] section"):
            load_config(temp_dir / "ossature.toml")

    def test_load_config_llm_without_model_raises(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
language = "python"

[llm]
audit = "anthropic:claude-opus-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        with pytest.raises(ConfigError, match="Missing 'model' in \\[llm\\]"):
            load_config(temp_dir / "ossature.toml")

    def test_tool_required_roles(self):
        assert "build" in TOOL_REQUIRED_ROLES
        assert "fixer" in TOOL_REQUIRED_ROLES
        assert "audit" not in TOOL_REQUIRED_ROLES
        assert "brief" not in TOOL_REQUIRED_ROLES

    def test_initialized_project_has_llm_section(self, initialized_project: Path):
        config_path = initialized_project / "ossature.toml"
        content = config_path.read_text()
        assert "[llm]" in content
        assert "model =" in content

    def test_build_command_invalid_type_raises(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "python"

[build]
setup = 42

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        with pytest.raises(ConfigError, match="build command must be a string or a list"):
            load_config(temp_dir / "ossature.toml")

    def test_warns_when_command_starts_with_cd_output_dir(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "python"

[build]
verify = ["cd output && pytest"]

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        with pytest.warns(UserWarning, match="cd output.*unnecessary"):
            load_config(temp_dir / "ossature.toml")

    def test_does_not_warn_when_cd_targets_subdirectory(self, temp_dir: Path):
        # `cd output_subdir` should not match `cd output`. The prefix check
        # only fires when the next character is a separator.
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "python"

[build]
verify = ["cd output_subdir && pytest"]

[llm]
model = "anthropic:claude-sonnet-4-6"
"""
        (temp_dir / "ossature.toml").write_text(config_content)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning would fail this test
            load_config(temp_dir / "ossature.toml")
