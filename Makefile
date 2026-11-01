# Repository set-up and the hook's tests; the other checks are uv
# commands (README.md).
hooks:
	git config core.hooksPath .githooks

hook-test:
	sh scripts/test-commit-msg.sh

.PHONY: hooks hook-test
