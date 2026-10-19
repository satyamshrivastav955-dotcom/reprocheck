.PHONY: test lint typecheck format-check build

UV ?= uv

test: lint typecheck format-check
	$(UV) run --locked python -m pytest

lint:
	$(UV) run --locked ruff check src tests

typecheck:
	$(UV) run --locked mypy src

format-check:
	$(UV) run --locked ruff format --check src tests

build:
	$(UV) build
