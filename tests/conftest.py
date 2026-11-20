"""Every test's git: its own repositories only.  A rule set for tests
only, newer than the package's."""

import subprocess
from pathlib import Path

import pytest

from ee_tooling import kicad_rules
from ee_tooling.profiles.jlcpcb import Jlcpcb

# git's variables that name a repository, its work tree, index or objects
# (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...), set by a hook or by
# `git rebase -x` running the suite: with one of them set, a test's
# `git init` or `git config` would act on that repository.
LOCAL_VARIABLES = subprocess.run(
    ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
).stdout.split()


@pytest.fixture(autouse=True)
def git_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No repository named by the environment; an empty global git config
    and no system one, so neither the machine's nor the user's (git-lfs's
    filters among them) is read and none is written; no repository found
    above pytest's temporary directory.  Returns the global config's
    path."""
    for name in LOCAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path_factory.mktemp("home") / "gitconfig"
    path.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # With no configuration git has no identity to commit or tag with.
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
    return path


# A rule set for tests only, newer than every set the package has, made
# from its BASE_SET: a rule's value changed, a rule retired and one added,
# a Board Setup minimum and an ERC severity raised.
BASE_SET = "0.4.0"
NEWER_SET = "99.0.0"
NEWER_DRU = (
    ("(constraint annular_width (min 0.18mm))", "(constraint annular_width (min 0.2mm))"),
    (
        '(rule "JLCPCB BGA pad size"\n'
        "\t(condition \"A.Type == 'Pad' && A.Fabrication_Property == 'BGA pad'\")\n"
        '\t(constraint assertion "A.Size_X >= 0.2mm && A.Size_Y >= 0.2mm"))\n',
        '(rule "Test only rule"\n\t(constraint track_width (min 0.2mm)))\n',
    ),
)
NEWER_BOARD_SETUP = ('"min_clearance": 0.1,', '"min_clearance": 0.12,')
NEWER_ERC = ('"field_name_whitespace": "warning"', '"field_name_whitespace": "error"')


@pytest.fixture
def newer_set(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """The package's rule sets and NEWER_SET, the current one; returns its
    version."""
    shipped = kicad_rules.rules_directory()
    sets = tmp_path_factory.mktemp("rules")
    for version in kicad_rules.rule_sets():
        (sets / version).mkdir()
        for f in shipped.joinpath(version).iterdir():
            (sets / version / f.name).write_text(f.read_text())
    newer = sets / NEWER_SET
    newer.mkdir()
    changes = {
        Jlcpcb.rules.dru: NEWER_DRU,
        Jlcpcb.rules.board_setup: (NEWER_BOARD_SETUP,),
        kicad_rules.ERC_SETTINGS: (NEWER_ERC,),
    }
    for name, pairs in changes.items():
        text = (sets / BASE_SET / name).read_text()
        for old, new in pairs:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        (newer / name).write_text(text)
    monkeypatch.setattr(kicad_rules, "rules_directory", lambda: sets)
    return NEWER_SET
