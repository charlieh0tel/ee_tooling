# Repository set-up, the hook's tests and mypy; the other checks are
# uv commands (README.md).
hooks:
	git config core.hooksPath .githooks

hook-test:
	sh scripts/test-commit-msg.sh

# mypy (strict) over src and tests (pyproject.toml), then over
# tools/odb_current, a script with dependencies of its own: their
# types, at the versions pinned here.
mypy:
	uv run mypy
	uv run --with matplotlib==3.11.2 --with numpy==2.5.5 \
	    --with scipy-stubs==1.18.1.1 \
	    --with types-shapely==2.2.0.20261122 \
	    mypy --strict tools/odb_current/odb_current.py

.PHONY: hooks hook-test mypy
