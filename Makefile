.PHONY: lint format mypy check docs docs-serve docs-check

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src

test:
	uv run pytest tests/ -v

test-cov:
	uv run pytest tests/ --cov=src/ossature --cov-report=term-missing --cov-report=html

check: lint typecheck test

docs:
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

docs-check: docs
	npx cspell "docs/**/*.md"
