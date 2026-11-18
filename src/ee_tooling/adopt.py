"""Put ee_tooling in place in a board repository (docs/adopting.md).

Runs from the board repository's ee_tooling submodule (its `adopt`
script), whatever the directory.  Without --yes it prints what it would
do; with --yes it does it; with --check it fails if a file it manages
differs from what this ee_tooling would write.  It never overwrites a
file the project owns once written, but to migrate one ee_tooling
renamed (jlcpcb.toml, the release's skill).
"""

import argparse
import importlib.resources
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ee_tooling.fab_release import CONFIG, DRU, FAB, GONE_KEYS, string, strings
from ee_tooling.kicad_pcbnew import SYSTEM_PYTHON, unsupported
from ee_tooling.kicad_rules import (
    ERC_SETTINGS,
    Dru,
    board_setup,
    erc_set,
    install,
    installed,
    raised,
    resource,
)
from ee_tooling.profiles import PROFILES
from ee_tooling.profiles.jlcpcb import Jlcpcb

# This ee_tooling's checkout: src/ee_tooling/adopt.py's grandparent.
CHECKOUT = Path(__file__).resolve().parents[2]
HOOKS_PATH = ".githooks"
AGENTS = Path("AGENTS.md")
AGENTS_BEGIN = "<!-- ee_tooling: begin; written by ee-tooling-adopt -->"
AGENTS_END = "<!-- ee_tooling: end -->"
# What a hand-adopted AGENTS.md has: the house style's import line.
STYLE = "docs/kicad-style.md"
# The profile of each board adopt finds.
PROFILE = Jlcpcb.name
# The release's skill in a board repository, from the root.
SKILL = Path(".claude/skills/fab-release/SKILL.md")
# Where adopt puts a lone board's releases, from the root.
PRODUCTION = Path("production")
# What ee_tooling renamed, in a board repository: the release's old config
# and its old [[board]] keys fab.toml has, the old skill's directory, and the names in
# it, old to new.
OLD_CONFIG = Path("jlcpcb.toml")
OLD_KEYS = ("name", "directory", "production", "sources")
OLD_SKILL = Path(".claude/skills/jlcpcb-release")
RENAMED = (("jlcpcb-release", "fab-release"), (str(OLD_CONFIG), CONFIG))
# A line of fab.toml that starts a [[board]], and one that starts its
# [board.fab].
BOARD_HEADER = re.compile(r"^[ \t]*\[\[[ \t]*board[ \t]*\]\][ \t]*(#.*)?$", re.M)
# A TOML one-line string, basic or literal.
TOML_STRING = re.compile(r""""(?:[^"\\]|\\.)*"|'[^']*'""")
FAB_HEADER = re.compile(rf"^[ \t]*\[[ \t]*board[ \t]*\.[ \t]*{FAB}[ \t]*\][ \t]*(#.*)?$", re.M)


class Owner(Enum):
    """Who keeps a file up to date once it is written."""

    MANAGED = "managed"  # ee-tooling-adopt: a rerun rewrites it, --check compares it
    PROJECT = "project"  # the board repository: written only if missing


@dataclass(frozen=True)
class File:
    """A file to write, from the repository root."""

    path: Path
    text: str
    owner: Owner
    executable: bool = False

    def action(self, root: Path) -> str | None:
        """create or update, or None when nothing is to be done."""
        target = root / self.path
        if not target.exists():
            return "create"
        if self.owner is Owner.MANAGED and target.read_text() != self.text:
            return "update"
        return None


@dataclass(frozen=True)
class Command:
    """A command to run, in a directory from the repository root."""

    args: tuple[str, ...]
    directory: Path = Path()


@dataclass(frozen=True)
class Remove:
    """A project file ee_tooling no longer reads, from the repository root."""

    path: Path
    why: str  # what replaced it


@dataclass(frozen=True)
class Rename:
    """A project directory ee_tooling renamed, from the repository root,
    with the names ee_tooling renamed renamed in its Markdown files."""

    source: Path
    target: Path
    names: tuple[tuple[str, str], ...]  # old, new


@dataclass(frozen=True)
class Edit:
    """A project file ee_tooling adds to, its own text kept, from the
    repository root."""

    path: Path
    text: str  # the file, edited
    why: str  # what was added


Step = File | Command | Remove | Rename | Edit


@dataclass(frozen=True)
class Layout:
    """Where ee_tooling and the hardware tools' environment are."""

    root: Path
    submodule: Path  # from the root

    @property
    def hardware(self) -> Path:
        """The hardware tools' uv project, the submodule's parent."""
        return self.submodule.parent

    def fill(self, template: str) -> str:
        text = importlib.resources.files(__package__).joinpath("templates", template).read_text()
        return (
            text.replace("@EE_TOOLING@", self.submodule.as_posix())
            .replace("@HARDWARE@", self.hardware.as_posix())
            .replace("@NAME@", self.root.name)
        )


def git(directory: Path, *args: str) -> str:
    """git's output, or "" if it fails (a missing key, no superproject)."""
    r = subprocess.run(["git", *args], cwd=directory, capture_output=True, text=True, check=False)
    return r.stdout.strip()


def locate(checkout: Path) -> Layout:
    """The board repository that has checkout as its submodule."""
    superproject = git(checkout, "rev-parse", "--show-superproject-working-tree")
    if not superproject:
        raise ValueError(f"{checkout} is not a submodule of a board repository")
    root = Path(superproject)
    return Layout(root, checkout.relative_to(root))


def toml_string(text: str) -> str:
    """text as a TOML basic string: JSON's escapes are TOML's."""
    return json.dumps(text)


def toml_value(value: object) -> str:
    """A string, number or boolean, as TOML writes it."""
    match value:
        case bool():
            return "true" if value else "false"
        case int() | float():
            return str(value)
        case str():
            return toml_string(value)
    raise TypeError(f"{value!r} is not a string, number or boolean")


def fab_lines(defaults: dict[str, Any], keys: Iterable[str]) -> list[str]:
    """[board.fab]'s keys, each with its house value."""
    return [f"{k} = {toml_value(defaults[k])}" for k in keys]


def board_lines(name: str, directory: str, production: str, sources: list[str]) -> list[str]:
    """A [[board]] of fab.toml, with adopt's profile and its house
    [board.fab] values."""
    defaults = PROFILES[PROFILE]().fab_defaults
    return [
        "",
        "[[board]]",
        f"name = {toml_string(name)}",
        f"directory = {toml_string(directory)}",
        f"production = {toml_string(production)}",
        f"sources = [{', '.join(toml_string(s) for s in sources)}]",
        f"profile = {toml_string(PROFILE)}",
        "",
        f"[board.{FAB}]",
        *fab_lines(defaults, defaults),
    ]


def fab_added(text: str) -> tuple[str, list[str]] | None:
    """fab.toml's text with the [board.fab] keys each board lacks added,
    with its profile's house values, its comments kept, and what was added;
    None if none lacks one.  A board whose profile is not known is left to
    fab-release.  ValueError if the text cannot be edited so: a fab given
    other than as a [board.fab] table, or a [[board]] not found by its
    header."""
    config = tomllib.loads(text)
    boards = config.get("board", [])
    if not isinstance(boards, list) or not all(isinstance(b, dict) for b in boards):
        return None
    # Each board's missing keys, and its house values.
    missing: list[tuple[list[str], dict[str, Any]]] = []
    for b in boards:
        found = b.get(FAB, {})
        if not isinstance(found, dict):
            raise ValueError(f"[[board]] {b.get('name')!r}: {FAB!r} is not a table")
        defaults = PROFILES[b["profile"]]().fab_defaults if b.get("profile") in PROFILES else {}
        missing.append(([k for k in defaults if k not in found], defaults))
    if not any(keys for keys, _ in missing):
        return None
    headers = list(BOARD_HEADER.finditer(text))
    if len(headers) != len(boards):
        raise ValueError(f"{len(boards)} boards, {len(headers)} [[board]] headers")
    ends = [h.start() for h in headers[1:]] + [len(text)]
    added = []
    for b, header, end, (keys, defaults) in reversed(
        list(zip(boards, headers, ends, missing, strict=True))
    ):
        if not keys:
            continue
        lines = "\n".join(fab_lines(defaults, keys))
        table = FAB_HEADER.search(text, header.end(), end)
        if table is not None:
            text = f"{text[: table.end()]}\n{lines}{text[table.end() :]}"
        elif FAB in b:
            raise ValueError(f"[[board]] {b.get('name')!r}: {FAB!r} is not a [board.{FAB}] table")
        else:
            at = header.start() + len(text[header.start() : end].rstrip())
            text = f"{text[:at]}\n\n[board.{FAB}]\n{lines}{text[at:]}"
        added.append(f"[[board]] {b.get('name')!r}: {', '.join(keys)}")
    after = tomllib.loads(text).get("board", [])
    if any(
        k not in b.get(FAB, {}) for b, (keys, _) in zip(after, missing, strict=True) for k in keys
    ):
        raise ValueError("the keys added are not where they belong")
    return text, list(reversed(added))


def productions(layout: Layout) -> list[Path]:
    """The production directories, from the root: adopt's, and each that
    jlcpcb.toml or fab.toml names."""
    out = [PRODUCTION]
    for config in (OLD_CONFIG, Path(CONFIG)):
        path = layout.root / config
        if not path.exists():
            continue
        try:
            boards = tomllib.loads(path.read_text()).get("board", [])
        except tomllib.TOMLDecodeError as e:
            print(f"{config}: {e}; its production directories not skipped", file=sys.stderr)
            continue
        out += [
            Path(b["production"])
            for b in boards
            if isinstance(b, dict) and isinstance(b.get("production"), str)
        ]
    return out


def projects(layout: Layout) -> list[Path]:
    """The KiCad projects (*.kicad_pro) under the hardware directory, but
    in the submodule, a production directory or a hidden directory (.git,
    .venv, .claude's worktrees)."""
    root = layout.root
    skip = {root / layout.submodule, *(root / p for p in productions(layout))}
    found = []
    for here, directories, names in (root / layout.hardware).walk():
        directories[:] = [d for d in directories if not d.startswith(".") and here / d not in skip]
        found += [here / n for n in names if n.endswith(".kicad_pro")]
    return sorted(found)


def fab_config(layout: Layout) -> str | None:
    """A starting fab.toml, one [[board]] per KiCad project under the
    hardware directory, or None if there is none."""
    found = projects(layout)
    if not found:
        return None
    lines = [
        "# The boards fab-release builds; the first is the default.  Paths are",
        "# from the repository root.  Written by ee-tooling-adopt: check each",
        "# board's production directory and [board.fab], and add its sources.",
    ]
    for project in found:
        # One board owns production/; several each get their own directory.
        production = PRODUCTION if len(found) == 1 else PRODUCTION / project.stem
        lines += board_lines(
            project.stem,
            project.parent.relative_to(layout.root).as_posix(),
            production.as_posix(),
            [],
        )
    return "\n".join(lines) + "\n"


def has_comment(text: str) -> bool:
    """Whether TOML text has a comment: a # outside a one-line string."""
    return any("#" in TOML_STRING.sub("", line) for line in text.splitlines())


def migrated_config(text: str) -> str:
    """fab.toml from jlcpcb.toml's text: its boards, each with adopt's
    profile.  Its comments are not carried over, nor its keys fab.toml does
    not have, each named on stderr.  ValueError naming a bad key."""
    old = tomllib.loads(text)
    lines = [
        "# The boards fab-release builds; the first is the default.  Paths are",
        f"# from the repository root.  Migrated from {OLD_CONFIG} by ee-tooling-adopt:",
        "# check each board's [board.fab].",
    ]
    if has_comment(text):
        print(f"{OLD_CONFIG}: its comments not carried over to {CONFIG}", file=sys.stderr)
    for key in sorted(set(old) - {"board"}):
        print(f"{OLD_CONFIG}: {key!r} not carried over to {CONFIG}", file=sys.stderr)
    boards = old.get("board", [])
    if not isinstance(boards, list) or not all(isinstance(b, dict) for b in boards):
        raise ValueError("no [[board]]")
    for number, table in enumerate(boards, 1):
        where = f"[[board]] {number}"
        name = string(where, table, "name", None)
        where = f"[[board]] {name!r}"
        for key in sorted(set(table) - set(OLD_KEYS)):
            why = f": {GONE_KEYS[key]}" if key in GONE_KEYS else ""
            print(
                f"{OLD_CONFIG}: {where}: {key!r} not carried over to {CONFIG}{why}", file=sys.stderr
            )
        lines += board_lines(
            name,
            string(where, table, "directory", None),
            string(where, table, "production", None),
            strings(where, table, "sources", []),
        )
    return "\n".join(lines) + "\n"


def migrations(layout: Layout) -> list[Remove | Rename | Edit]:
    """What moves the project files ee_tooling renamed to their new names
    (a file's new text is among files()), and adds the keys fab.toml
    lacks.  ValueError naming what cannot be done."""
    root = layout.root
    out: list[Remove | Rename | Edit] = []
    if (root / CONFIG).exists():
        try:
            edited = fab_added((root / CONFIG).read_text())
        except ValueError as e:  # tomllib.TOMLDecodeError among them
            raise ValueError(f"{CONFIG}: {e}; add each [board.{FAB}] key by hand") from e
        if edited is not None:
            text, added = edited
            out.append(Edit(Path(CONFIG), text, f"[board.{FAB}] keys added: {'; '.join(added)}"))
    for old, new in ((OLD_CONFIG, Path(CONFIG)), (OLD_SKILL, SKILL.parent)):
        if not (root / old).exists():
            continue
        if (root / new).exists():
            print(f"{old} and {new}: {old} is not read; remove it", file=sys.stderr)
        elif old == OLD_CONFIG:
            out.append(Remove(old, f"migrated to {new}"))
        else:
            out.append(Rename(old, new, RENAMED))
    return out


def project_json(project: dict[str, Any]) -> str:
    """A .kicad_pro's text, indented as KiCad writes it."""
    return json.dumps(project, indent=2, ensure_ascii=False) + "\n"


def rules_edits(root: Path, config: str | None) -> list[Edit]:
    """What installs each board's profile's rules in its .kicad_dru, raises
    its Board Setup in its .kicad_pro and sets its ERC severities (kept
    where stricter) and connection grid there, by fab.toml's text (config),
    each checked once edited.  A board whose profile is not known, or whose
    .kicad_pro is not there, is left, said on stderr.  ValueError naming
    what cannot be done."""
    if config is None:
        return []
    try:
        boards = tomllib.loads(config).get("board", [])
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{CONFIG}: {e}") from e
    out = []
    for b in boards if isinstance(boards, list) else []:
        if not isinstance(b, dict) or b.get("profile") not in PROFILES:
            continue
        name, directory = b.get("name"), b.get("directory")
        if not isinstance(name, str) or not isinstance(directory, str):
            continue
        profile = PROFILES[b["profile"]]()
        try:
            options = profile.options(b.get(profile.name, {}))
        except ValueError as e:
            raise ValueError(f"{CONFIG}: [[board]] {name!r}: [board.{profile.name}]: {e}") from e
        project, dru = Path(directory, f"{name}.kicad_pro"), Path(directory, f"{name}{DRU}")
        if not (root / project).exists():
            print(f"{project.as_posix()} is not there: rules not installed", file=sys.stderr)
            continue
        section = installed(profile.name, profile.rules, options)
        least = board_setup(profile.rules)
        house = json.loads(resource(ERC_SETTINGS))
        by_hand = "install the rules by hand"
        try:
            settings = json.loads((root / project).read_text())
        except ValueError as e:
            raise ValueError(f"{project.as_posix()}: {e}; {by_hand}") from e
        existing = (root / dru).read_text() if (root / dru).exists() else None
        try:
            found = install(existing, settings, section, least)
        except ValueError as e:
            raise ValueError(f"{dru.as_posix()}: {e}; {by_hand}") from e
        settings, erc = erc_set(found.project, house)
        if (
            Dru.split(found.dru).section != section
            or raised(settings, least)[1]
            or erc_set(settings, house)[1]
        ):
            raise ValueError(f"[[board]] {name!r}: the rules are not where they belong; {by_hand}")
        if found.copied:
            print(
                f"{dru.as_posix()}: rules {', '.join(found.copied)} after ee_tooling's copy them"
                " and win over them: remove them",
                file=sys.stderr,
            )
        close = "close the board in KiCad first"
        if found.stale:
            out.append(
                Edit(dru, found.dru, f"ee_tooling's {profile.name} rules installed; {close}")
            )
        what = []
        if found.raised:
            what.append(
                f"Board Setup raised to {profile.name}'s minimums: {'; '.join(found.raised)}"
            )
        if erc:
            what.append(f"ERC severities and connection grid set ({len(erc)} values)")
        if what:
            out.append(Edit(project, project_json(settings), f"{'; '.join(what)}; {close}"))
    return out


def agents_text(existing: str | None, block: str) -> str | None:
    """AGENTS.md with ee_tooling's section, or None if it was adopted by
    hand (the house style imported outside the markers)."""
    section = f"{AGENTS_BEGIN}\n{block.rstrip()}\n{AGENTS_END}\n"
    if existing is None:
        return section
    begin, end = existing.find(AGENTS_BEGIN), existing.find(AGENTS_END)
    if begin >= 0 and end > begin:
        return existing[:begin] + section + existing[end + len(AGENTS_END) :].lstrip("\n")
    if STYLE in existing:
        return None
    return existing.rstrip("\n") + "\n\n" + section


def uses_ee_tooling(pyproject: Path) -> bool:
    if not pyproject.exists():
        return False
    sources = tomllib.loads(pyproject.read_text()).get("tool", {}).get("uv", {}).get("sources", {})
    return "ee-tooling" in sources


def system_site_packages(venv: Path) -> bool:
    config = venv / "pyvenv.cfg"
    return config.exists() and "include-system-site-packages = true" in config.read_text()


def files(layout: Layout) -> list[File]:
    """The files ee_tooling puts in a board repository."""
    root = layout.root
    out = [
        File(Path(HOOKS_PATH, "commit-msg"), layout.fill("commit-msg"), Owner.MANAGED, True),
        File(
            Path(".github/workflows/ee-tooling.yml"), layout.fill("ee-tooling.yml"), Owner.MANAGED
        ),
        File(
            layout.hardware / "pyproject.toml",
            layout.fill("hardware-pyproject.toml"),
            Owner.PROJECT,
        ),
    ]
    agents = root / AGENTS
    text = agents_text(agents.read_text() if agents.exists() else None, layout.fill("agents.md"))
    if text is None:
        print(f"{AGENTS}: adopted by hand; left as it is", file=sys.stderr)
    else:
        out.append(File(AGENTS, text, Owner.MANAGED))
    # A skill to be renamed (migrations()) is not written anew.
    if not (root / OLD_SKILL).exists() or (root / SKILL.parent).exists():
        out.append(File(SKILL, layout.fill("fab-release-skill.md"), Owner.PROJECT))
    if (root / CONFIG).exists():
        return out
    if (root / OLD_CONFIG).exists():
        try:
            config = migrated_config((root / OLD_CONFIG).read_text())
        except (tomllib.TOMLDecodeError, ValueError) as e:
            raise ValueError(f"{OLD_CONFIG}: {e}; fix it, or write {CONFIG} by hand") from e
        out.append(File(Path(CONFIG), config, Owner.PROJECT))
        return out
    new = fab_config(layout)
    if new is None:
        print(f"no KiCad project under {layout.hardware}; no {CONFIG}", file=sys.stderr)
    else:
        out.append(File(Path(CONFIG), new, Owner.PROJECT))
    return out


def plan(layout: Layout) -> list[Step]:
    """What is to be done, in order."""
    root, submodule, hardware = layout.root, layout.submodule, layout.hardware
    planned = files(layout)
    steps: list[Step] = [f for f in planned if f.action(root)]
    steps += migrations(layout)
    config = next((f.text for f in planned if f.path == Path(CONFIG) and f.action(root)), None)
    steps += rules_edits(root, config if config is not None else read_config(root))
    venv = root / hardware / ".venv"
    reason = unsupported()
    if reason is not None:
        print(
            f"{reason}: no venv on the system Python, so stitch and placement-score"
            " cannot run here (docs/stitch.md, Platforms)",
            file=sys.stderr,
        )
    elif not system_site_packages(venv):
        clear = ("--clear",) if venv.exists() else ()
        steps.append(
            Command(
                ("uv", "venv", *clear, "--system-site-packages", "--python", SYSTEM_PYTHON),
                hardware,
            )
        )
    if not uses_ee_tooling(root / hardware / "pyproject.toml"):
        editable = f"./{submodule.relative_to(hardware).as_posix()}"
        steps.append(
            Command(("uv", "add", "--dev", "--editable", "--no-workspace", editable), hardware)
        )
    if git(root, "config", "core.hooksPath") != HOOKS_PATH:
        steps.append(Command(("git", "config", "core.hooksPath", HOOKS_PATH)))
    return steps


def describe(step: Step, root: Path) -> str:
    match step:
        case File():
            return f"{step.action(root)} {step.path.as_posix()} ({step.owner.value})"
        case Command():
            where = f" (in {step.directory.as_posix()})" if step.directory != Path() else ""
            return f"run {' '.join(step.args)}{where}"
        case Remove():
            return f"remove {step.path.as_posix()} (project; {step.why})"
        case Edit():
            return f"update {step.path.as_posix()} (project; {step.why})"
        case Rename():
            names = ", ".join(f"{old} -> {new}" for old, new in step.names)
            return (
                f"move {step.source.as_posix()}/ to {step.target.as_posix()}/"
                f" (project; {names} in its .md files)"
            )


def apply(root: Path, steps: Sequence[Step], run: Callable[[Sequence[str], Path], None]) -> None:
    for step in steps:
        print(describe(step, root), flush=True)
        match step:
            case File():
                target = root / step.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(step.text)
                if step.executable:
                    target.chmod(0o755)
            case Command():
                run(step.args, root / step.directory)
            case Remove():
                (root / step.path).unlink()
            case Edit():
                (root / step.path).write_text(step.text)
            case Rename():
                (root / step.source).rename(root / step.target)
                for f in sorted((root / step.target).rglob("*.md")):
                    text = f.read_text()
                    for old, new in step.names:
                        text = text.replace(old, new)
                    f.write_text(text)


def run_command(args: Sequence[str], directory: Path) -> None:
    # Not in the venv adopt runs in (its submodule's), but the directory's own.
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(args, cwd=directory, env=environment, check=True)


def read_config(root: Path) -> str | None:
    """fab.toml's text, or None if it is not there."""
    path = root / CONFIG
    return path.read_text() if path.exists() else None


def pending(layout: Layout) -> list[tuple[str, Path]]:
    """What --check fails on, each with what it awaits: the managed files
    that differ from what would be written ("out of date"), the project
    files still to be migrated ("to be migrated"), and the project files
    whose rules or settings are still to be installed ("to be updated")."""
    managed = [f.path for f in files(layout) if f.owner is Owner.MANAGED and f.action(layout.root)]
    renamed = [s.source if isinstance(s, Rename) else s.path for s in migrations(layout)]
    rules = [s.path for s in rules_edits(layout.root, read_config(layout.root))]
    return [
        *(("out of date", p) for p in managed),
        *(("to be migrated", p) for p in renamed),
        *(("to be updated", p) for p in rules),
    ]


def stale(layout: Layout) -> list[Path]:
    """The paths pending() names."""
    return [p for _, p in pending(layout)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--yes", action="store_true", help="do it, rather than print it")
    mode.add_argument("--check", action="store_true", help="fail if a managed file is stale")
    a = ap.parse_args()

    try:
        layout = locate(CHECKOUT)
        if a.check:
            out_of_date = pending(layout)
        else:
            steps = plan(layout)
    except ValueError as e:
        sys.exit(str(e))
    if a.check:
        for what, p in out_of_date:
            print(f"{what}: {p.as_posix()}; run {layout.submodule}/adopt --yes", file=sys.stderr)
        sys.exit(1 if out_of_date else 0)
    if not steps:
        print("nothing to do")
    elif a.yes:
        try:
            apply(layout.root, steps, run_command)
        except subprocess.CalledProcessError as e:
            sys.exit(f"{' '.join(e.cmd)} failed ({e.returncode})")
    else:
        for step in steps:
            print(describe(step, layout.root))
        print("rerun with --yes to do it")
