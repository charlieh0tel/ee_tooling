"""A file replaced whole or not at all."""

import os
import re
import stat
from pathlib import Path

# The scratch file a write makes beside its target, and how to know one a
# killed write left: .NAME.PID.ee_tooling-tmp.
TEMPORARY = ".{name}.{pid}.ee_tooling-tmp"
LEFTOVER = re.compile(r"^\..+\.\d+\.ee_tooling-tmp$")


def write_bytes(target: Path, data: bytes) -> None:
    """target's contents replaced whole or not at all: written beside it,
    then renamed over it, its mode kept (a new file's from the umask).  A
    symbolic link is written through: its target is written, the link
    kept.  A file with other hard links is written in place, so every name
    keeps sharing it: its old contents are lost if the write fails
    midway, which a scratch copy written first and kept beside it until
    the write is done makes recoverable."""
    target = target.resolve()
    temporary = target.with_name(TEMPORARY.format(name=target.name, pid=os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if target.exists() and target.stat().st_nlink > 1:
            with target.open("r+b") as f:
                f.write(data)
                f.truncate()
            return
        if target.exists():
            temporary.chmod(stat.S_IMODE(target.stat().st_mode))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_text(target: Path, text: str) -> None:
    """write_bytes of text in UTF-8, its line endings as given."""
    write_bytes(target, text.encode("utf-8"))
