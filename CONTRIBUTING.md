# Contributing to Ossature

Thanks for wanting to contribute. This page covers the practical stuff you need to get going.

## Setup

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ossature/ossature.git
cd ossature
uv sync --group dev
```

That installs the project along with dev dependencies (ruff, mypy, pytest, etc).

## Running checks

There's a Makefile that wraps the common commands:

```bash
make check      # runs lint + typecheck + tests
make test       # just tests
make lint       # ruff check + format check
make typecheck  # mypy
make format     # auto-fix lint and formatting
```

Run `make check` before submitting a PR. CI runs the same thing.

## Making changes

Fork the repo, create a branch, make your changes, run `make check`, and open a PR against `master`.

Keep PRs focused. One logical change per PR is easier to review than a grab bag of unrelated fixes. If you're planning something large or architectural, open an issue first so we can talk about the approach before you invest a lot of time.

## Tests

Tests live in `tests/`. Run them with `make test` or `uv run pytest tests/ -v` directly.

If you're adding a new feature, add tests for it. If you're fixing a bug, a regression test that reproduces the bug is very helpful.

## Code style

Ruff handles linting and formatting. The config is in `pyproject.toml`. Mypy runs in strict mode. The pre-commit hooks catch most issues automatically but running `make check` before pushing is a good habit.

Don't worry too much about getting everything perfect on the first try, that's what review is for.

## AI-generated code

Using AI tools to help write code is fine. But if you submit it, you own it. Review what you're submitting, make sure it works, make sure you understand it. The bar for quality is the same regardless of how the code was written.

## Issues

If you find a bug or have a feature request, open an issue. For bugs include what you did, what you expected, and what happened instead. Version info and a minimal reproduction help a lot.

## License

By contributing you agree that your contributions will be licensed under the MIT License.
