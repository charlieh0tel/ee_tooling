"""KiCad's pcbnew module, which only the system Python has (docs/stitch.md).

stitch and placement-score import pcbnew; missing() says why they cannot.
They need a system Python of MIN_PYTHON or later: ee_tooling cannot run
on an older one, and no other Python has pcbnew.  unsupported() says why
the system Python cannot, for adopt too.
"""

import subprocess
import sys

SYSTEM_PYTHON = "/usr/bin/python3"
# pyproject.toml's requires-python, as a version_info prefix.
MIN_PYTHON = (3, 12)


def version(version_info: tuple[int, ...]) -> str:
    return ".".join(map(str, version_info))


def system_python() -> tuple[int, int] | None:
    """The system Python's major and minor version; None without one."""
    try:
        out = subprocess.run(
            (SYSTEM_PYTHON, "-c", "import sys; print(*sys.version_info[:2])"),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"{SYSTEM_PYTHON}: {e}", file=sys.stderr)
        return None
    major, minor = map(int, out.split())
    return major, minor


def unsupported() -> str | None:
    """Why the system Python cannot run what imports pcbnew; None if it can."""
    found = system_python()
    if found is None:
        return f"there is no {SYSTEM_PYTHON}"
    if found < MIN_PYTHON:
        return (
            f"KiCad's pcbnew is in the system Python only, {SYSTEM_PYTHON} is"
            f" {version(found)}, and ee_tooling needs {version(MIN_PYTHON)} or later"
        )
    return None


def missing() -> str:
    """Why pcbnew is not importable, and what to do."""
    reason = unsupported()
    if reason is not None:
        return f"pcbnew is not importable: {reason}, so this cannot run here (docs/stitch.md)"
    return (
        "pcbnew is not importable: make the venv with"
        f" uv venv --system-site-packages --python {SYSTEM_PYTHON}"
    )
