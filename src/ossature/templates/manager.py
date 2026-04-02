from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import ClassVar


@dataclass
class TemplateResult:
    created: list[Path]
    skipped: list[Path]
    errors: list[str]

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class TemplateLoader:
    _cache: ClassVar[dict[str, str]] = {}

    @classmethod
    def get(cls, name: str) -> str:
        if name in cls._cache:
            return cls._cache[name]

        template_file = f"{name}.template"

        try:
            files = resources.files("ossature.templates") / "files" / template_file
            content = files.read_text(encoding="utf-8")

        except FileNotFoundError, TypeError:
            template_path = Path(__file__).parent / "files" / template_file

            if not template_path.exists():
                raise FileNotFoundError(f"Template not found: {template_file}") from None

            content = template_path.read_text(encoding="utf-8")

        cls._cache[name] = content
        return content


class TemplateManager:
    def __init__(self, root: Path):
        self.root = root
        self.loader = TemplateLoader

    def init_project(
        self,
        name: str,
        spec_dir: str = "specs",
        context_dir: str = "context",
    ) -> TemplateResult:
        created: list[Path] = []
        skipped: list[Path] = []
        errors: list[str] = []

        dirs = [
            self.root / spec_dir,
            self.root / context_dir,
        ]

        for dir_path in dirs:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Failed to create {dir_path}: {e}")

        config_path = self.root / "ossature.toml"
        if config_path.exists():
            skipped.append(config_path)
        else:
            try:
                template = self.loader.get("config")
                content = template.format(name=name)
                config_path.write_text(content, encoding="utf-8")
                created.append(config_path)
            except Exception as e:
                errors.append(f"Failed to create ossature.toml: {e}")

        gitignore_path = self.root / ".gitignore"
        if gitignore_path.exists():
            skipped.append(gitignore_path)
        else:
            try:
                content = self.loader.get("gitignore")
                gitignore_path.write_text(content, encoding="utf-8")
                created.append(gitignore_path)
            except Exception as e:
                errors.append(f"Failed to create .gitignore: {e}")

        return TemplateResult(created=created, skipped=skipped, errors=errors)
