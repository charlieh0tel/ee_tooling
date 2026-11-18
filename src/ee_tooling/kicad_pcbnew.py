"""KiCad's pcbnew module, which only the system Python has (docs/stitch.md).

stitch and placement-score import pcbnew; missing() says why they cannot.
"""

SYSTEM_PYTHON = "/usr/bin/python3"


def missing() -> str:
    """Why pcbnew is not importable, and what to do."""
    return (
        "pcbnew is not importable: make the venv with"
        f" uv venv --system-site-packages --python {SYSTEM_PYTHON}"
    )
