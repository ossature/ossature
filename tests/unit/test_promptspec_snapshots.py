"""Snapshot tests for the registered PromptSpec set.

Each fixture under tests/unit/fixtures/promptspec is the expected
rendered output for one (spec id, language) pair. Calling `render()`
again must produce the same bytes, so any change to a prompt or
profile that flows through to the rendered text shows up as a
fixture diff for review.
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
    # Normalize trailing newlines on both sides. The end-of-file-fixer
    # pre-commit hook tends to append a final newline to committed text
    # files, but trailing whitespace is meaningless to the LLM and not
    # part of what the original Final[str] constants produced.
    assert rendered.rstrip("\n") == expected.rstrip("\n"), (
        f"render({spec_id!r}, language={language!r}) drifted from fixture {fixture_path.name}"
    )
