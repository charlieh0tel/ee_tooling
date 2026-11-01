"""Put ee_tooling in place in a board repository (docs/adopting.md).

Runs from the board repository's ee_tooling submodule (its `adopt`
script), whatever the directory.  Without --yes it prints what it would
do; with --yes it does it; with --check it fails if a file it manages
differs from what this ee_tooling would write.  It never overwrites a
file the project owns once written.
"""

import argparse
import importlib.resources
import math
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ee_tooling.jlcpcb_release import CONFIG

# This ee_tooling's checkout: src/ee_tooling/adopt.py's grandparent.
CHECKOUT = Path(__file__).resolve().parents[2]
SYSTEM_PYTHON = "/usr/bin/python3"
HOOKS_PATH = ".githooks"
AGENTS = Path("AGENTS.md")
AGENTS_BEGIN = "<!-- ee_tooling: begin; written by ee-tooling-adopt -->"
AGENTS_END = "<!-- ee_tooling: end -->"
# What a hand-adopted AGENTS.md has: the house style's import line.
STYLE = "docs/kicad-style.md"


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


Step = File | Command


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


def copper_layers(pcb_text: str) -> list[str]:
    """A board file's copper layers, top to bottom."""
    layers = re.search(r"^\s*\(layers\b(.*?)^\s*\)", pcb_text, re.S | re.M)
    names = re.findall(r'^\s*\(\d+ "((?:F|B|In\d+)\.Cu)"', layers.group(1), re.M) if layers else []

    def depth(name: str) -> float:
        side = name.removesuffix(".Cu")
        return 0 if side == "F" else math.inf if side == "B" else int(side.removeprefix("In"))

    return sorted(names, key=depth)


def jlcpcb_config(layout: Layout) -> str | None:
    """A starting jlcpcb.toml, one [[board]] per KiCad project under the
    hardware directory, or None if there is none."""
    projects = sorted(
        p
        for p in (layout.root / layout.hardware).rglob("*.kicad_pro")
        if not p.is_relative_to(layout.root / layout.submodule)
    )
    if not projects:
        return None
    lines = [
        "# The boards jlcpcb-release builds; the first is the default.  Paths are",
        "# from the repository root.  Written by ee-tooling-adopt: check each",
        "# board's production directory and add its sources.",
    ]
    for n, project in enumerate(projects):
        pcb = project.with_suffix(".kicad_pcb")
        copper = copper_layers(pcb.read_text()) if pcb.exists() else []
        if not copper:
            why = "no copper layers in it" if pcb.exists() else "not there"
            print(f"{pcb.relative_to(layout.root)}: {why}; copper left empty", file=sys.stderr)
        production = "production" if n == 0 else f"production/{project.stem}"
        lines += [
            "",
            "[[board]]",
            f'name = "{project.stem}"',
            f'directory = "{project.parent.relative_to(layout.root).as_posix()}"',
            f"copper = [{', '.join(f'"{c}"' for c in copper)}]",
            f'production = "{production}"',
            "sources = []",
        ]
    return "\n".join(lines) + "\n"


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
            Path(".claude/skills/jlcpcb-release/SKILL.md"),
            layout.fill("jlcpcb-release-skill.md"),
            Owner.PROJECT,
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
    config = jlcpcb_config(layout)
    if config is None:
        print(f"no KiCad project under {layout.hardware}; no {CONFIG}", file=sys.stderr)
    else:
        out.append(File(Path(CONFIG), config, Owner.PROJECT))
    return out


def plan(layout: Layout) -> list[Step]:
    """What is to be done, in order."""
    root, submodule, hardware = layout.root, layout.submodule, layout.hardware
    steps: list[Step] = [f for f in files(layout) if f.action(root)]
    venv = root / hardware / ".venv"
    if not system_site_packages(venv):
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


def run_command(args: Sequence[str], directory: Path) -> None:
    # Not in the venv adopt runs in (its submodule's), but the directory's own.
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(args, cwd=directory, env=environment, check=True)


def stale(layout: Layout) -> list[Path]:
    """The managed files that differ from what would be written."""
    return [f.path for f in files(layout) if f.owner is Owner.MANAGED and f.action(layout.root)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--yes", action="store_true", help="do it, rather than print it")
    mode.add_argument("--check", action="store_true", help="fail if a managed file is stale")
    a = ap.parse_args()

    try:
        layout = locate(CHECKOUT)
    except ValueError as e:
        sys.exit(str(e))
    if a.check:
        out_of_date = stale(layout)
        for p in out_of_date:
            print(
                f"out of date: {p.as_posix()}; run {layout.submodule}/adopt --yes", file=sys.stderr
            )
        sys.exit(1 if out_of_date else 0)
    steps = plan(layout)
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
