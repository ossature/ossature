"""Snapshot tests for the shipped PromptSpec set.

Each captured fixture was rendered from the previous Final[str] prompt
constants. Rendered output from the new system must match those fixtures
byte-for-byte, which proves the port doesn't change what the LLM sees.
"""

from pathlib import Path

import pytest

from ossature.promptspec import render

FIXTURES = Path(__file__).parent / "fixtures" / "promptspec"


def _fixture_cases() -> list[tuple[str, str | None, Path]]:
    cases: list[tuple[str, str | None, Path]] = []
    for fp in sorted(FIXTURES.glob("*.txt")):
        stem = fp.stem
        if "__" in stem:
            spec_id, language = stem.split("__", 1)
            cases.append((spec_id, language, fp))
        else:
            cases.append((stem, None, fp))
    return cases


@pytest.mark.parametrize(("spec_id", "language", "fixture_path"), _fixture_cases())
def test_render_matches_fixture(spec_id: str, language: str | None, fixture_path: Path) -> None:
    rendered = render(spec_id, language=language) if language else render(spec_id)
    expected = fixture_path.read_text()
    assert rendered == expected, (
        f"render({spec_id!r}, language={language!r}) drifted from fixture {fixture_path.name}"
    )
