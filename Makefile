.PHONY: lint format mypy check docs docs-serve docs-check update-deps

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src

test:
	uv run pytest tests/ -v $(ARGS)

test-cov:
	rm -f .coverage
	uv run pytest tests/unit --cov=src/ossature --cov-report=xml:coverage-unit.xml --junitxml=junit-unit.xml -o junit_family=legacy
	uv run pytest tests/integration --cov=src/ossature --cov-append --cov-report=term-missing --cov-report=html --cov-report=xml:coverage-integration.xml --junitxml=junit-integration.xml -o junit_family=legacy

check: lint typecheck test

docs:
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

docs-check: docs
	npx cspell "docs/**/*.md"

update-deps:
	uv lock --upgrade
	uv sync
