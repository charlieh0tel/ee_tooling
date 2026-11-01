#!/bin/sh
# The commit-msg hook's checks (.githooks/commit-msg), on sample
# identities and messages through its --identities and --messages
# modes.  Run by `make hook-test` and CI.
set -u

HOOK=$(dirname "$0")/../.githooks/commit-msg
SAMPLE=$(mktemp)
trap 'rm -f "$SAMPLE"' EXIT
failures=0

# expect pass|refuse MODE TEXT: the hook in MODE on TEXT passes or
# refuses it.
expect() {
    printf '%s\n' "$3" >"$SAMPLE"
    if sh "$HOOK" "--$2" "$SAMPLE" 2>/dev/null; then
        result=pass
    else
        result=refuse
    fi
    if [ "$result" != "$1" ]; then
        echo "FAIL: expected $1, got $result: --$2 '$3'" >&2
        failures=$((failures + 1))
    fi
}

expect pass identities 'Christopher Hoover <ch@murgatroid.com>'
expect pass identities 'Claude Monet <cm@example.com>'
expect pass identities 'Claude Debussy <claude@example.com>'
expect pass identities 'Jean-Claude Code <jc@example.com>'
expect pass identities 'Claude Codex Smith <cs@example.com>'
expect pass identities 'Ann Smith <ann@notanthropic.com>'
expect pass identities 'Ann Smith <anthropic.com@example.com>'
expect refuse identities 'Claude <noreply@anthropic.com>'
expect refuse identities 'Claude Code <x@example.com>'
expect refuse identities 'claude bot <x@example.com>'
expect refuse identities 'Claude Assistant <x@example.com>'
expect refuse identities 'Claude <x@example.com>'
expect refuse identities 'claude[bot] <x@example.com>'
expect refuse identities 'Ann Smith <ann@anthropic.com>'
expect refuse identities 'Ann Smith <ann@mail.Anthropic.com>'
expect refuse identities 'Anthropic Agent <x@example.com>'
# As `git var` gives an identity, with the time after the address.
expect refuse identities 'Claude Code <x@example.com> 1700000000 +0000'
expect pass identities 'Claude Monet <cm@example.com> 1700000000 +0000'
# One bot line among people refuses the lot.
expect refuse identities 'Claude Monet <cm@example.com>
Claude <noreply@anthropic.com>'

expect pass messages 'Fix the BOM rotation'
expect pass messages 'Credit Claude Monet in the examples'
expect pass messages '# Co-Authored-By: an editor comment'
expect refuse messages 'Fix it

Co-Authored-By: Claude <noreply@anthropic.com>'
expect refuse messages 'Fix it

Generated with [Claude Code](https://claude.ai/code)'
expect refuse messages 'Fix it

Claude-Session: https://claude.ai/code/session_x'

# The hook as a board repository runs it: ee-tooling-adopt's wrapper
# (src/ee_tooling/templates/commit-msg) as its .githooks/commit-msg,
# the hook in its ee_tooling submodule.
CONSUMER=$(mktemp -d)
trap 'rm -f "$SAMPLE"; rm -rf "$CONSUMER"' EXIT
git init -q "$CONSUMER"
mkdir -p "$CONSUMER/.githooks" "$CONSUMER/hardware/ee_tooling/.githooks" "$CONSUMER/subdirectory"
sed "s|@EE_TOOLING@|hardware/ee_tooling|" "$(dirname "$0")/../src/ee_tooling/templates/commit-msg" \
    >"$CONSUMER/.githooks/commit-msg"
chmod +x "$CONSUMER/.githooks/commit-msg"
git -C "$CONSUMER" config core.hooksPath .githooks

# expect_commit pass|refuse NAME MESSAGE: a commit by NAME with MESSAGE,
# made from a subdirectory, passes or is refused.
expect_commit() {
    if (cd "$CONSUMER/subdirectory" &&
        GIT_AUTHOR_NAME="$2" GIT_AUTHOR_EMAIL=a@example.com \
            GIT_COMMITTER_NAME="$2" GIT_COMMITTER_EMAIL=a@example.com \
            git commit -q --allow-empty -m "$3") 2>/dev/null; then
        result=pass
    else
        result=refuse
    fi
    if [ "$result" != "$1" ]; then
        echo "FAIL: expected $1, got $result: a consumer's commit by '$2': '$3'" >&2
        failures=$((failures + 1))
    fi
}

# Before `git submodule update --init` the hook is missing.
expect_commit refuse 'Ann Smith' 'Fix the BOM rotation'
cp "$HOOK" "$CONSUMER/hardware/ee_tooling/.githooks/commit-msg"
expect_commit pass 'Ann Smith' 'Fix the BOM rotation'
expect_commit refuse 'Claude Code' 'Fix the BOM rotation'
expect_commit refuse 'Ann Smith' 'Fix it

Co-Authored-By: Claude <noreply@anthropic.com>'

if [ "$failures" -ne 0 ]; then
    echo "commit-msg tests: $failures failed" >&2
    exit 1
fi
