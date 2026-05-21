import pytest
from pydantic import ValidationError

from ossature.models.plan import PlannerTask, PlanTask


def _make_plan_task(**overrides) -> dict:
    base = {
        "id": "001",
        "spec": "AUDIO",
        "title": "Copy SFX",
        "description": "Bundle audio assets",
        "outputs": ["src/assets/*.mp3"],
        "depends_on": [],
        "spec_refs": [],
        "arch_refs": [],
        "verify": [],
    }
    base.update(overrides)
    return base


def _make_planner_task(**overrides) -> dict:
    base = {
        "title": "Copy SFX",
        "description": "Bundle audio assets",
        "outputs": ["src/assets/*.mp3"],
        "depends_on": [],
        "spec_refs": [],
        "arch_refs": [],
        "verify": [],
    }
    base.update(overrides)
    return base


class TestSourceFieldDefaults:
    def test_plan_task_default_source_empty(self):
        task = PlanTask(**_make_plan_task())
        assert task.source == []

    def test_planner_task_default_source_empty(self):
        task = PlannerTask(**_make_planner_task())
        assert task.source == []


class TestSourceFieldNormalization:
    def test_strips_context_prefix(self):
        task = PlanTask(**_make_plan_task(source=["context://assets/audio/*.mp3"]))
        assert task.source == ["assets/audio/*.mp3"]

    def test_accepts_path_without_prefix(self):
        task = PlanTask(**_make_plan_task(source=["assets/audio/foo.mp3"]))
        assert task.source == ["assets/audio/foo.mp3"]

    def test_accepts_single_string_as_list(self):
        task = PlanTask(**_make_plan_task(source="context://assets/foo.mp3"))
        assert task.source == ["assets/foo.mp3"]

    def test_accepts_none_as_empty(self):
        task = PlanTask(**_make_plan_task(source=None))
        assert task.source == []

    def test_normalizes_multiple_entries(self):
        task = PlanTask(**_make_plan_task(source=["context://a/b.mp3", "c/d.mp3"]))
        assert task.source == ["a/b.mp3", "c/d.mp3"]


class TestSourceFieldValidation:
    def test_rejects_foreign_scheme(self):
        with pytest.raises(ValidationError) as exc_info:
            PlanTask(**_make_plan_task(source=["file:///etc/passwd"]))
        assert "scheme" in str(exc_info.value).lower()

    def test_rejects_http_scheme(self):
        with pytest.raises(ValidationError):
            PlanTask(**_make_plan_task(source=["http://example.com/x.mp3"]))

    def test_rejects_absolute_path(self):
        with pytest.raises(ValidationError) as exc_info:
            PlanTask(**_make_plan_task(source=["/abs/path.mp3"]))
        assert "absolute" in str(exc_info.value).lower()

    def test_rejects_traversal(self):
        with pytest.raises(ValidationError) as exc_info:
            PlanTask(**_make_plan_task(source=["../escape/foo.mp3"]))
        assert "traversal" in str(exc_info.value).lower() or ".." in str(exc_info.value)

    def test_rejects_traversal_mid_path(self):
        with pytest.raises(ValidationError):
            PlanTask(**_make_plan_task(source=["assets/../etc/passwd"]))

    def test_rejects_empty_string_entry(self):
        with pytest.raises(ValidationError):
            PlanTask(**_make_plan_task(source=[""]))

    def test_rejects_scheme_only(self):
        with pytest.raises(ValidationError):
            PlanTask(**_make_plan_task(source=["context://"]))

    def test_planner_task_same_validation(self):
        with pytest.raises(ValidationError):
            PlannerTask(**_make_planner_task(source=["file:///etc/passwd"]))

    def test_rejects_non_string_non_list_type(self):
        with pytest.raises(Exception, match="source must be a string or a list"):
            PlanTask(**_make_plan_task(source=42))
