"""The fabs a board can be released for, one module each (the interface
in profile.py): internal, not public API."""

from collections.abc import Callable
from typing import Any

from ee_tooling.profiles.jlcpcb import Jlcpcb
from ee_tooling.profiles.profile import Profile

# Each profile's maker, by its name: a release makes its profile before
# anything is checked or written, as loading the profile's data can fail.
PROFILES: dict[str, Callable[[], Profile[Any]]] = {Jlcpcb.name: Jlcpcb.builtin}
