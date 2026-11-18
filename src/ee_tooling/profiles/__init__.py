"""The fabs a board can be released for, one module each (the interface
in profile.py): internal, not public API."""

from collections.abc import Callable

from ee_tooling.profiles.jlcpcb import Jlcpcb
from ee_tooling.profiles.profile import Profile

# Each profile's maker, by its name: a release makes its profile before
# anything is checked or written, as loading the profile's data can fail.
PROFILES: dict[str, Callable[[], Profile]] = {Jlcpcb.name: Jlcpcb.builtin}
# The profile of the boards in jlcpcb.toml.
DEFAULT = Jlcpcb.name
