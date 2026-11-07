"""kicad-style-check's waivers: findings the board's owner has judged
intentional, each with its reason (docs/kicad-style-check.md, Waivers).

A waiver names a rule's id and every one of its subject fields
(kicad_style_finding.RULES), and a reason.  It waives the findings of
that rule whose subject is equal on every field: a set field as a set, a
position within POSITION_TOLERANCE.  A field given a list of values (a
list of lists for a set, of [x, y] for a position) waives each, and
each must match a finding.
"""

import itertools
import math
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass

from ee_tooling.kicad_style_finding import POSITION_FIELD, RULES, SET_FIELDS, Finding, Subject

# The waivers' file, beside the checked file.
WAIVERS = "kicad-style.toml"
# A waiver's position matches a finding's this close, mm: the precision
# findings print.
POSITION_TOLERANCE = 0.01
# A waiver's keys besides its subject fields.
RULE, REASON = "rule", "reason"


class WaiverError(ValueError):
    """A waivers file that is not one: an unknown rule, a missing or
    unexpected field, an empty reason."""


@dataclass(frozen=True)
class Waiver:
    """One waived subject of a rule, and why."""

    rule: str
    subject: tuple[tuple[str, Subject], ...]  # the rule's fields, in its order
    reason: str

    def __str__(self) -> str:
        return f"{self.rule} {'; '.join(f'{k}={show(v)}' for k, v in self.subject)}".rstrip()


def show(value: Subject) -> str:
    """A subject value as a waiver's line prints it."""
    if isinstance(value, frozenset):
        return ", ".join(sorted(value))
    if isinstance(value, tuple):
        return f"({value[0]:g}, {value[1]:g})"
    return value


def position(value: object) -> tuple[float, float] | None:
    """[x, y] as a position, else None."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        return float(value[0]), float(value[1])
    return None


def names(value: object) -> frozenset[str] | None:
    """A list of strings as a set, else None."""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return frozenset(value)
    return None


def values(field: str, value: object) -> list[Subject]:
    """A field's value, or each of a list of them."""
    if field == POSITION_FIELD:
        one = position(value)
        many = [position(v) for v in value] if isinstance(value, list) else []
        if one is not None:
            return [one]
        if many and all(many):
            return [p for p in many if p is not None]
        raise WaiverError(f"{field}: not [x, y] or a list of them")
    if field in SET_FIELDS:
        one_set = names(value)
        sets = [names(v) for v in value] if isinstance(value, list) else []
        if one_set:
            return [one_set]
        if sets and all(sets):
            return [s for s in sets if s]
        raise WaiverError(f"{field}: not a list of names or a list of such lists")
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        return list(value)
    raise WaiverError(f"{field}: not a name or a list of names")


def load(text: str) -> list[Waiver]:
    """A waivers file's waivers, one per subject."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise WaiverError(str(e)) from e
    extra = set(data) - {"waiver"}
    if extra:
        raise WaiverError(f"unexpected {', '.join(sorted(extra))}: only [[waiver]]")
    entries = data.get("waiver", [])
    if not isinstance(entries, list):
        raise WaiverError("waiver: not an array of tables, [[waiver]]")
    out = []
    for n, entry in enumerate(entries, 1):
        what = f"waiver {n}"
        if not isinstance(entry, dict):
            raise WaiverError(f"{what}: not a table")
        rule = entry.get(RULE)
        if rule not in RULES:
            raise WaiverError(f"{what}: unknown rule {rule!r}")
        reason = entry.get(REASON)
        if not isinstance(reason, str) or not reason.strip():
            raise WaiverError(f"{what} ({rule}): no reason")
        fields = RULES[rule]
        given = set(entry) - {RULE, REASON}
        if given != set(fields):
            missing = ", ".join(sorted(set(fields) - given)) or "none"
            unexpected = ", ".join(sorted(given - set(fields))) or "none"
            raise WaiverError(f"{what} ({rule}): missing {missing}; unexpected {unexpected}")
        try:
            options = [values(f, entry[f]) for f in fields]
        except WaiverError as e:
            raise WaiverError(f"{what} ({rule}): {e}") from e
        out += [
            Waiver(rule, tuple(zip(fields, chosen, strict=True)), reason.strip())
            for chosen in itertools.product(*options)
        ]
    return out


def matches(waiver: Waiver, finding: Finding) -> bool:
    if waiver.rule != finding.rule:
        return False
    for (_, want), (_, have) in zip(waiver.subject, finding.subject, strict=True):
        if isinstance(want, tuple) and isinstance(have, tuple):
            if math.dist(want, have) > POSITION_TOLERANCE:
                return False
        elif want != have:
            return False
    return True


def waive(
    waivers: list[Waiver], findings: Iterable[Finding]
) -> tuple[list[Finding], list[tuple[Finding, Waiver]], set[int]]:
    """findings not waived, those waived with their waivers, and the
    indices of the waivers that matched any."""
    kept, waived, used = [], [], set()
    for f in findings:
        hits = [i for i, w in enumerate(waivers) if matches(w, f)]
        if hits:
            waived.append((f, waivers[hits[0]]))
            used |= set(hits)
        else:
            kept.append(f)
    return kept, waived, used
