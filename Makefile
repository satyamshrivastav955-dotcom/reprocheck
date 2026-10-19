.PHONY: test lint typecheck format-check build

UV ?= uv

test: lint typecheck format-check
	$(UV) run --locked --offline python -m pytest

lint:
	$(UV) run --locked --offline ruff check src tests

typecheck:
	$(UV) run --locked --offline mypy src

format-check:
	$(UV) run --locked --offline ruff format --check src tests

build:
	$(UV) build
