"""kicad-style-check's findings and its rules' ids (docs/kicad-style-check.md).

A finding names its rule and its subject, the fields that say what it is
about: a net, a pad as "R1 pad 2", a part's reference, a layer, a set of
them, or a position where nothing else names it.
"""

from dataclasses import dataclass

Point = tuple[float, float]  # mm

# A finding for the owner to judge, not a broken rule: printed, but not
# counted in kicad-style-check's exit status.
NOTE = "note: "

# A finding's subject value: a name (a net, a pad as "R1 pad 2", a part's
# reference), a set of names (the pads a via serves, a pair's nets), or a
# position, mm.
Subject = str | frozenset[str] | Point

# The schematic rules: each id and its subject's fields.
SCHEMATIC_RULES = {
    "off-grid": ("at",),
    "power-on-pin": ("pins",),
    "pins-touching": ("pins",),
    "pin-on-wire": ("pins",),
    "pin-no-wire": ("pin",),
    "wires-join-at-pin": ("pins",),
    "pin-wire-short": ("pins",),
    "join-no-junction": ("at",),
    "junction-not-at-join": ("at",),
    "no-connect-off-pin": ("at",),
    "zero-length-wire": ("at",),
    "overlapping-wires": ("at",),
    "part-fields": ("reference",),
    "capacitor-value": ("reference",),
}

# The zone rules: each id and its subject's fields.
ZONE_RULES = {
    "filled-shape": ("at",),
    "no-outline": (),
    "zone-unlocked": ("net", "layers"),
    "zone-on-board": ("net", "layers"),
}

# The finishing rules: each id and its subject's fields.
FINISHING_RULES = {
    "stitching-unchecked": (),
    "outline-open": ("at",),
    "stitching-gap": ("net", "at"),
    "mounting-hole-no-drill": ("reference",),
    "mounting-hole-unplated": ("reference",),
    "mounting-hole-ring": ("reference",),
    "mounting-hole-no-net": ("reference",),
}

# The stackup table rules: each id and its subject's fields.
STACKUP_RULES = {
    "stackup-missing": (),
    "stackup-table-missing": (),
    "stackup-table-layer": ("layer",),
    "stackup-table-rows": (),
    "stackup-table-row": ("layer",),
}

# The routing rules: each id and its subject's fields.
ROUTING_RULES = {
    "decoupling-no-pin": ("capacitor", "pin"),
    "decoupling-no-pad": ("capacitor", "pin"),
    "via-off-plane": ("net", "pad"),
    "stub-direction": ("net", "pad"),
    "stub-length": ("net", "pad"),
    "thermal-vias": ("net", "pad"),
    "big-pad-vias": ("net", "pad"),
    "pad-no-via": ("net", "pad"),
    "via-shared": ("net", "pads"),
    "trace-pad-to-pad": ("net", "pads"),
    "supply-no-copper": ("pin", "capacitor"),
    "supply-bypass": ("pin",),
    "supply-unfed": ("pin", "capacitor"),
}

# The net class rules: each id and its subject's fields.
NETCLASS_RULES = {
    "class-impedance-name": ("netclass",),
    "class-undefined": ("netclass",),
    "power-net-class": ("net",),
    "half-pair": ("net",),
    "pair-class": ("nets",),
}

# Every rule: its id and its subject's fields, in order.
RULES = (
    SCHEMATIC_RULES | ZONE_RULES | FINISHING_RULES | STACKUP_RULES | ROUTING_RULES | NETCLASS_RULES
)
# Subject fields that hold a set of names, matched as a set.
SET_FIELDS = frozenset({"pins", "pads", "nets", "layers"})
# The subject field that holds a position, for a finding nothing else names.
POSITION_FIELD = "at"


@dataclass(frozen=True)
class Finding:
    """What a checker found: its rule's id, what it is about (the rule's
    subject fields and their values), what is wrong, and whether it is a
    note."""

    rule: str
    subject: tuple[tuple[str, Subject], ...]
    message: str
    note: bool = False

    def __str__(self) -> str:
        return f"{NOTE}{self.message}" if self.note else self.message


def found(rule: str, message: str, note: bool = False, **subject: Subject) -> Finding:
    """A finding of rule, whose subject must have the rule's fields."""
    if tuple(subject) != RULES[rule]:
        raise ValueError(f"{rule}: subject {tuple(subject)}, not {RULES[rule]}")
    return Finding(rule, tuple(subject.items()), message, note)


@dataclass(frozen=True)
class Checked:
    """A checker's findings, and the ids of the rules it ran: not those it
    skipped, as routing is on a board with no tracks."""

    findings: list[Finding]
    ran: frozenset[str]
