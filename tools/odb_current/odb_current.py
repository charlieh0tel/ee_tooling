#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "shapely>=2", "matplotlib"]
# ///
"""DC resistance and current-capacity analysis of one net in an ODB++ export.

Builds the copper of a net on every copper layer (positive signal layers from
the EDA feature IDs, negative plane layers by reconstructing the plane region
that touches the net's holes), rasterises it, couples the layers with via /
pin barrel resistors and solves the 2-D conduction problem with a sparse
direct solver.

  odb_current.py info  ODB [--grep PATTERN]
  odb_current.py pins  ODB REFDES [REFDES ...]
  odb_current.py solve ODB --net NET --from J1:1,3,5 --to J7:2 [--to U5] ...

Terminals are REFDES or REFDES:pin,pin. All pins of one terminal are tied
together (equipotential). The first --to is the reference for the plots,
hotspots and via currents; every --to gets its two-terminal resistance from
--from.
"""

import argparse
import atexit
import difflib
import json
import math
import re
import shutil
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

RHO_20C = 1.724e-5  # ohm*mm, annealed copper at 20 C
ALPHA_CU = 0.00393  # 1/C
MIL2_PER_MM2 = 1 / 0.00064516


# --------------------------------------------------------------------------
# ODB++ reading


def _lines(path):
    with open(path, errors="replace") as f:
        for line in f:
            yield line.rstrip("\r\n")


def find_root(odb):
    """Return the ODB++ job directory (the one holding matrix/matrix)."""
    p = Path(odb)
    if p.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="odb_current_"))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        with tarfile.open(p) as t:
            t.extractall(tmp, filter="data")
        p = tmp
    hits = sorted(p.rglob("matrix/matrix"), key=lambda q: len(q.parts))
    if not hits:
        sys.exit(f"no matrix/matrix under {p}: not an ODB++ job")
    return hits[0].parent.parent


def parse_blocks(path):
    """Parse matrix-style 'NAME { K=V ... }' blocks."""
    out, cur = [], None
    for line in _lines(path):
        s = line.strip()
        if s.endswith("{"):
            cur = {"_kind": s[:-1].strip()}
        elif s == "}" and cur is not None:
            out.append(cur)
            cur = None
        elif cur is not None and "=" in s:
            k, v = s.split("=", 1)
            cur[k.strip()] = v.strip()
    return out


def read_attrs(path):
    d = {}
    if path.exists():
        for line in _lines(path):
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


class Job:
    def __init__(self, odb, step=None):
        self.root = find_root(odb)
        steps = sorted(p.name for p in (self.root / "steps").iterdir())
        self.step = step or steps[0]
        self.sdir = self.root / "steps" / self.step
        blocks = parse_blocks(self.root / "matrix" / "matrix")
        self.layers = sorted(
            [b for b in blocks if b["_kind"] == "LAYER"], key=lambda b: int(b["ROW"])
        )
        for b in self.layers:
            b["name"] = b["NAME"].lower()
            b["attrs"] = read_attrs(self.sdir / "layers" / b["name"] / "attrlist")
        self._stackup()
        self._eda()

    # stackup ----------------------------------------------------------
    def _stackup(self):
        self.copper = []  # dicts: name, t (mm), z (mm, centre), negative
        z = 0.0
        for b in self.layers:
            if b.get("CONTEXT") != "BOARD":
                continue
            typ = b.get("TYPE")
            a = b["attrs"]
            if typ in ("SIGNAL", "POWER_GROUND", "MIXED"):
                w = float(a.get(".copper_weight", "0") or 0)
                # Altium writes microns here; true ODB uses ounces.
                t = w / 1000 if w > 5 else (w or 1.0) * 0.0348
                self.copper.append(
                    {
                        "name": b["name"],
                        "t": t,
                        "z": z + t / 2,
                        "negative": b.get("POLARITY") == "NEGATIVE",
                        "type": typ,
                    }
                )
                z += t
            elif typ == "DIELECTRIC":
                z += float(a.get(".layer_dielectric", "0") or 0)
        self.thickness = z
        for i, c in enumerate(self.copper):
            c["outer"] = i in (0, len(self.copper) - 1)

    def set_copper(self, overrides):
        """Override copper thickness (microns) per layer and recompute z."""
        for c in self.copper:
            if c["name"] in overrides:
                c["t"] = overrides[c["name"]] / 1000
        by_name = {c["name"]: c for c in self.copper}
        z = 0.0
        for b in self.layers:
            if b.get("CONTEXT") != "BOARD":
                continue
            if b["name"] in by_name:
                c = by_name[b["name"]]
                c["z"] = z + c["t"] / 2
                z += c["t"]
            elif b.get("TYPE") == "DIELECTRIC":
                z += float(b["attrs"].get(".layer_dielectric", "0") or 0)
        self.thickness = z

    def drill_layers(self):
        names = [c["name"] for c in self.copper]
        out = []
        for b in self.layers:
            if b.get("TYPE") != "DRILL" or "non-plated" in b["name"]:
                continue
            s, e = b.get("START_NAME", "").lower(), b.get("END_NAME", "").lower()
            if s in names and e in names:
                i, j = sorted((names.index(s), names.index(e)))
            else:
                i, j = 0, len(names) - 1
            out.append((b["name"], i, j))
        return out

    # EDA ----------------------------------------------------------------
    def _eda(self):
        self.eda_layers = []
        self.nets = []
        self.net_fids = []
        for line in _lines(self.sdir / "eda" / "data"):
            if line.startswith("LYR "):
                self.eda_layers = [s.lower() for s in line.split()[1:]]
            elif line.startswith("NET "):
                self.nets.append(line.split()[1].split(";")[0])
                self.net_fids.append({})
            elif line.startswith("FID ") and self.nets:
                _, _typ, li, fi = line.split()[:4]
                lyr = self.eda_layers[int(li)]
                self.net_fids[-1].setdefault(lyr, set()).add(int(fi))

    def fids(self, net):
        try:
            return self.net_fids[self.nets.index(net)]
        except ValueError:

            def sig(n):  # letters as a set, numbers as a set: V12_IN ~ VIN_12V
                n = n.lower()
                return set(re.findall(r"[a-z]", n)), set(re.findall(r"\d+", n))

            want = sig(net)
            close = [n for n in self.nets if sig(n) == want]
            close += difflib.get_close_matches(net, self.nets, n=5, cutoff=0.5)
            sys.exit(f"net {net!r} not found. Similar: {sorted(set(close))[:20]}")

    def components(self):
        """{refdes: dict(side, x, y, part, props, pins=[(name, x, y, net)])}"""
        comps = {}
        for side in ("top", "bot"):
            f = self.sdir / "layers" / f"comp_+_{side}" / "components"
            if not f.exists():
                continue
            cur = None
            for line in _lines(f):
                p = line.split(";")[0].split()
                if not p:
                    continue
                if p[0] == "CMP":
                    cur = {
                        "side": side,
                        "x": float(p[2]),
                        "y": float(p[3]),
                        "part": p[7],
                        "props": {},
                        "pins": [],
                    }
                    comps[p[6]] = cur
                elif p[0] == "PRP" and cur is not None:
                    cur["props"][p[1]] = " ".join(p[2:]).strip("'")
                elif p[0] == "TOP" and cur is not None:
                    net = self.nets[int(p[6])] if int(p[6]) < len(self.nets) else "?"
                    cur["pins"].append((p[8], float(p[2]), float(p[3]), net))
        return comps

    def profile(self):
        from shapely.ops import unary_union

        syms, feats, units = self.parse_features(self.sdir / "profile")
        return unary_union(
            [
                g
                for g in (feature_geom(syms, f, units, self)[0] for f in feats)
                if g is not None
            ]
        )

    # features -----------------------------------------------------------
    def parse_features(self, path):
        syms, feats, cur, units = {}, [], None, "MM"
        for line in _lines(path):
            if not line or line[0] == "#":
                continue
            if line.startswith("UNITS="):
                units = line.split("=")[1].strip().upper()
                continue
            if line[0] == "$":
                i, name = line[1:].split()[:2]
                syms[int(i)] = name
                continue
            tok = line.split(";")[0].split()
            if not tok:
                continue
            k = tok[0]
            if k in ("P", "L", "A", "T", "B"):
                feats.append((k, tok))
                cur = None
            elif k == "S":
                cur = ["S", tok, []]
                feats.append(cur)
            elif k in ("OB", "OS", "OC", "OE", "SE") and cur is not None:
                cur[2].append(tok)
        return syms, feats, units

    def layer_features(self, name):
        return self.parse_features(self.sdir / "layers" / name / "features")


# --------------------------------------------------------------------------
# geometry

_SYMCACHE = {}
_NOTED = set()


def _arc(xs, ys, xe, ye, xc, yc, cw, per_rad=20):
    a0 = math.atan2(ys - yc, xs - xc)
    a1 = math.atan2(ye - yc, xe - xc)
    r = math.hypot(xs - xc, ys - yc)
    if cw:
        if a1 >= a0 - 1e-12:
            a1 -= 2 * math.pi
    elif a1 <= a0 + 1e-12:
        a1 += 2 * math.pi
    n = max(4, int(abs(a1 - a0) * per_rad))
    return [
        (
            xc + r * math.cos(a0 + (a1 - a0) * i / n),
            yc + r * math.sin(a0 + (a1 - a0) * i / n),
        )
        for i in range(1, n + 1)
    ]


def sym_shape(name, units, job):
    """Symbol at the origin in mm. Returns (shape, stroke width, round?)."""
    from shapely import affinity
    from shapely.geometry import Point, Polygon, box
    from shapely.ops import unary_union

    s = 0.001 if units == "MM" else 0.0254  # symbol dims: microns or mils -> mm
    num = r"([\d.]+)"

    def rrect(w, h, r):
        r = min(r, w / 2, h / 2)
        if r <= 0:
            return box(-w / 2, -h / 2, w / 2, h / 2)
        return box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, 16)

    if m := re.fullmatch(rf"r{num}", name):
        d = float(m[1]) * s
        return Point(0, 0).buffer(d / 2, 32), d, True
    if m := re.fullmatch(rf"s{num}", name):
        d = float(m[1]) * s
        return box(-d / 2, -d / 2, d / 2, d / 2), d, False
    if m := re.fullmatch(rf"rect{num}x{num}(?:x([rc]){num}(?:x\d+)?)?", name):
        w, h = float(m[1]) * s, float(m[2]) * s
        r = float(m[4]) * s if m[3] else 0
        if m[3] == "c":  # chamfered: approximate with rounded corners
            r *= 0.6
        return rrect(w, h, r), min(w, h), False
    if m := re.fullmatch(rf"oval{num}x{num}", name):
        w, h = float(m[1]) * s, float(m[2]) * s
        return rrect(w, h, min(w, h) / 2), min(w, h), True
    if m := re.fullmatch(rf"di{num}x{num}", name):
        w, h = float(m[1]) * s, float(m[2]) * s
        return (
            Polygon([(w / 2, 0), (0, h / 2), (-w / 2, 0), (0, -h / 2)]),
            min(w, h),
            False,
        )
    if m := re.fullmatch(rf"oct{num}x{num}x{num}", name):
        w, h, c = (float(m[i]) * s for i in (1, 2, 3))
        return (
            Polygon(
                [
                    (-w / 2 + c, h / 2),
                    (w / 2 - c, h / 2),
                    (w / 2, h / 2 - c),
                    (w / 2, -h / 2 + c),
                    (w / 2 - c, -h / 2),
                    (-w / 2 + c, -h / 2),
                    (-w / 2, -h / 2 + c),
                    (-w / 2, h / 2 - c),
                ]
            ),
            min(w, h),
            False,
        )
    if m := re.fullmatch(rf"el{num}x{num}", name):
        w, h = float(m[1]) * s, float(m[2]) * s
        return affinity.scale(Point(0, 0).buffer(0.5, 32), w, h), min(w, h), True
    if m := re.fullmatch(rf"donut_r{num}x{num}", name):
        od, i_d = float(m[1]) * s, float(m[2]) * s
        return (
            Point(0, 0).buffer(od / 2, 32).difference(Point(0, 0).buffer(i_d / 2, 32)),
            od,
            True,
        )
    if m := re.fullmatch(rf"thr{num}x{num}x{num}x(\d+)x{num}", name):
        # round thermal: annulus id..od with n gaps of width gap, first at angle
        od, i_d, ang, n, gap = (
            float(m[1]) * s,
            float(m[2]) * s,
            float(m[3]),
            int(m[4]),
            float(m[5]) * s,
        )
        g = Point(0, 0).buffer(od / 2, 64).difference(Point(0, 0).buffer(i_d / 2, 64))
        for k in range(n):
            bar = affinity.rotate(
                box(0, -gap / 2, od, gap / 2), ang + k * 360 / n, origin=(0, 0)
            )
            g = g.difference(bar)
        return g, od, True
    if m := re.fullmatch(rf"ths{num}x{num}x{num}x(\d+)x{num}", name):
        od, i_d, ang, n, gap = (
            float(m[1]) * s,
            float(m[2]) * s,
            float(m[3]),
            int(m[4]),
            float(m[5]) * s,
        )
        g = box(-od / 2, -od / 2, od / 2, od / 2).difference(
            box(-i_d / 2, -i_d / 2, i_d / 2, i_d / 2)
        )
        for k in range(n):
            bar = affinity.rotate(
                box(0, -gap / 2, od, gap / 2), ang + k * 360 / n, origin=(0, 0)
            )
            g = g.difference(bar)
        return g, od, False
    if m := re.fullmatch(rf"uds_oblong_th{num}x{num}x.*", name):
        # Altium oblong thermal: treated as a solid oval (slightly optimistic)
        w, h = float(m[1]) * s, float(m[2]) * s
        if name not in _NOTED:
            _NOTED.add(name)
            print(f"  note: thermal {name} treated as a solid oval", file=sys.stderr)
        return rrect(w, h, min(w, h) / 2), min(w, h), True
    # user-defined symbol
    sdir = job.root / "symbols" / name
    if (sdir / "features").exists():
        if name not in _SYMCACHE:
            syms, feats, u = job.parse_features(sdir / "features")
            pos, neg = [], []
            for f in feats:
                g, pol = feature_geom(syms, f, u, job)
                if g is not None:
                    (pos if pol == "P" else neg).append(g)
            g = unary_union(pos)
            if neg:
                g = g.difference(unary_union(neg))
            b = g.bounds
            _SYMCACHE[name] = (g, min(b[2] - b[0], b[3] - b[1]), False)
        return _SYMCACHE[name]
    raise ValueError(f"unsupported symbol {name}")


def _orient(shape, rest):
    from shapely import affinity

    if not rest:
        return shape
    o = int(rest[0])
    if o in (8, 9):
        ang, mir = float(rest[1]), o == 9
    else:
        ang, mir = (o % 4) * 90, o >= 4
    if mir:
        shape = affinity.scale(shape, -1, 1, origin=(0, 0))
    return affinity.rotate(shape, -ang, origin=(0, 0))  # ODB rotation is clockwise


def feature_geom(syms, f, units, job):
    from shapely import affinity
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union

    c = 1.0 if units == "MM" else 25.4
    if f[0] == "S":
        _, tok, cont = f
        outers, holes, ring, kind = [], [], None, "I"
        for t in cont:
            if t[0] == "OB":
                ring, kind = [(float(t[1]) * c, float(t[2]) * c)], t[3]
            elif t[0] == "OS":
                ring.append((float(t[1]) * c, float(t[2]) * c))
            elif t[0] == "OC":
                xs, ys = ring[-1]
                ring += _arc(
                    xs,
                    ys,
                    float(t[1]) * c,
                    float(t[2]) * c,
                    float(t[3]) * c,
                    float(t[4]) * c,
                    t[5] == "Y",
                )
            elif t[0] == "OE" and ring and len(ring) > 2:
                (outers if kind == "I" else holes).append(Polygon(ring).buffer(0))
        g = unary_union(outers)
        if holes:
            g = g.difference(unary_union(holes))
        return g, tok[1]
    k, tok = f
    try:
        if k == "P":
            sh, _, _ = sym_shape(syms[int(tok[3])], units, job)
            return affinity.translate(
                _orient(sh, tok[6:]), float(tok[1]) * c, float(tok[2]) * c
            ), tok[4]
        if k == "L":
            xs, ys, xe, ye = (float(v) * c for v in tok[1:5])
            sh, w, rnd = sym_shape(syms[int(tok[5])], units, job)
            ls = (
                LineString([(xs, ys), (xe, ye)])
                if (xs, ys) != (xe, ye)
                else Point(xs, ys)
            )
            return ls.buffer(w / 2, 16, cap_style="round" if rnd else "square"), tok[6]
        if k == "A":
            xs, ys, xe, ye, xc, yc = (float(v) * c for v in tok[1:7])
            sh, w, _ = sym_shape(syms[int(tok[7])], units, job)
            cw = len(tok) > 10 and tok[10] == "Y"
            return LineString([(xs, ys)] + _arc(xs, ys, xe, ye, xc, yc, cw)).buffer(
                w / 2, 16
            ), tok[8]
    except ValueError as e:
        print(f"  warning: {e}; feature skipped", file=sys.stderr)
    return None, "P"


# --------------------------------------------------------------------------
# net geometry


def net_geometry(job, net):
    """Per copper layer: shapely geometry of the net, plus net holes."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    fids = job.fids(net)
    holes = []  # (x, y, d, first_layer_idx, last_layer_idx)
    foreign = []
    for dname, i, j in job.drill_layers():
        syms, feats, units = job.layer_features(dname)
        c = 1.0 if units == "MM" else 25.4
        mine = fids.get(dname, set())
        for fi, f in enumerate(feats):
            if f[0] != "P":
                continue
            sym = syms[int(f[1][3])]
            m = re.fullmatch(r"r([\d.]+)", sym)
            if not m:
                continue
            d = float(m[1]) * (0.001 if units == "MM" else 0.0254)
            h = (float(f[1][1]) * c, float(f[1][2]) * c, d, i, j)
            (holes if fi in mine else foreign).append(h)
    print(f"net {net}: {len(holes)} plated holes", file=sys.stderr)
    prof = None
    geom = {}
    for li, cu in enumerate(job.copper):
        name = cu["name"]
        syms, feats, units = job.layer_features(name)
        if not cu["negative"]:
            pos, neg = [], []
            for i in sorted(fids.get(name, ())):
                g, pol = feature_geom(syms, feats[i], units, job)
                if g is not None:
                    (pos if pol == "P" else neg).append(g)
            g = unary_union(pos)
            if neg:
                g = g.difference(unary_union(neg))
            geom[name] = g if not g.is_empty else None
            continue
        # negative plane: copper = profile minus every feature; keep regions
        # that touch one of the net's holes (no antipad there = connected)
        if prof is None:
            prof = job.profile()
        cut = [feature_geom(syms, f, units, job)[0] for f in feats]
        cu_g = prof.difference(unary_union([g for g in cut if g is not None]))
        parts = list(getattr(cu_g, "geoms", [cu_g]))
        mine = [h for h in holes if h[3] <= li <= h[4]]
        keep = [
            p
            for p in parts
            if any(
                p.intersects(Point(x, y).buffer(d / 2 + 0.05)) for x, y, d, *_ in mine
            )
        ]
        if keep:
            g = unary_union(keep)
            bad = [
                h
                for h in foreign
                if h[3] <= li <= h[4]
                and g.intersects(Point(h[0], h[1]).buffer(h[2] / 2))
            ]
            msg = f"  plane {name}: {len(keep)} region(s), {g.area:.0f} mm2 connected to {net}"
            if bad:
                msg += f"  ** WARNING: {len(bad)} other-net holes also connect; check plane assignment **"
            print(msg, file=sys.stderr)
            geom[name] = g
        else:
            geom[name] = None
    return geom, holes


# --------------------------------------------------------------------------
# solver


def ipc2221(area_mm2, dt, outer):
    k = 0.048 if outer else 0.024
    return k * dt**0.44 * (area_mm2 * MIL2_PER_MM2) ** 0.725


def parse_term(s, comps, net):
    """REFDES[:pin,pin] -> (ref, pins). A bare REFDES means its pins on the net."""
    ref, _, pins = s.partition(":")
    if ref not in comps:
        sys.exit(f"component {ref} not found")
    if pins:
        want = set(pins.split(","))
        sel = [p for p in comps[ref]["pins"] if p[0] in want]
    else:
        sel = [p for p in comps[ref]["pins"] if p[3] == net]
    if not sel:
        sys.exit(f"{ref}: no pins selected on {net}")
    return ref, sel


def solve(args):
    import scipy.sparse as sp
    import scipy.sparse.linalg as spl
    import shapely
    from shapely.geometry import Point, box

    job = Job(args.odb, args.step)
    if args.cu:
        job.set_copper(
            {k.lower(): float(v) for k, v in (s.split("=") for s in args.cu)}
        )
    comps = job.components()
    geom, holes = net_geometry(job, args.net)
    h, plate, rho = args.grid, args.plating, RHO_20C
    terms = [("from", *parse_term(args.src, comps, args.net))] + [
        ("to", *parse_term(t, comps, args.net)) for t in args.to
    ]
    for role, ref, pins in terms:
        bad = [p for p in pins if p[3] != args.net]
        if bad:
            print(
                f"  warning: {ref} pins {[p[0] for p in bad]} are not on {args.net}: {[p[3] for p in bad]}",
                file=sys.stderr,
            )
    allg = shapely.union_all([g for g in geom.values() if g is not None])
    x0, y0, x1, y1 = args.bbox or allg.bounds
    x0, y0, x1, y1 = x0 - 0.2, y0 - 0.2, x1 + 0.2, y1 + 0.2
    crop = box(x0, y0, x1, y1)
    xs = np.arange(x0 + h / 2, x1, h)
    ys = np.arange(y0 + h / 2, y1, h)
    XX, YY = np.meshgrid(xs, ys)
    holes = [hh for hh in holes if crop.intersects(Point(hh[0], hh[1]))]
    layers = [c for c in job.copper if geom.get(c["name"]) is not None]
    idx, masks, N = [], [], 0
    for cu in layers:
        g = geom[cu["name"]].intersection(crop)
        li = job.copper.index(cu)
        m = shapely.contains_xy(g, XX, YY)
        for x, y, d, a, b in holes:
            if a <= li <= b and g.intersects(Point(x, y).buffer(d / 2 + 0.03)):
                m |= (XX - x) ** 2 + (YY - y) ** 2 <= (d / 2) ** 2
        ids = -np.ones(m.shape, int)
        ids[m] = np.arange(N, N + m.sum())
        N += int(m.sum())
        idx.append(ids)
        masks.append(m)
        print(
            f"  {cu['name']:<14} t={cu['t'] * 1000:6.2f} um z={cu['z']:.3f} mm cells={m.sum()}",
            file=sys.stderr,
        )
    R, C, V = [], [], []

    def add(a, b, g):
        a, b = np.atleast_1d(a), np.atleast_1d(b)
        g = np.broadcast_to(g, a.shape)
        R.append(np.r_[a, b, a, b])
        C.append(np.r_[a, b, b, a])
        V.append(np.r_[g, g, -g, -g])

    for cu, ids in zip(layers, idx):
        for a, b in ((ids[:, :-1], ids[:, 1:]), (ids[:-1, :], ids[1:, :])):
            k = (a >= 0) & (b >= 0)
            add(a[k], b[k], cu["t"] / rho)
    BIG = 1e4
    nxt = [N]

    def node():
        nxt[0] += 1
        return nxt[0] - 1

    barrel = {}  # (hole, layer_i) -> node
    vias = []  # (hole_i, upper node, lower node, R, area)
    for hi, (x, y, d, a, b) in enumerate(holes):
        prev = None
        for li, cu in enumerate(layers):
            gli = job.copper.index(cu)
            if not a <= gli <= b:
                continue
            c = idx[li][((XX - x) ** 2 + (YY - y) ** 2 <= (d / 2) ** 2) & masks[li]]
            if len(c) == 0:
                continue
            n = node()
            barrel[(hi, li)] = n
            add(c, np.full(len(c), n), BIG)
            if prev:
                area = math.pi * (d - plate) * plate
                r = rho * (cu["z"] - prev[0]) / area
                add(np.array([prev[1]]), np.array([n]), 1 / r)
                vias.append((hi, prev[1], n, r, area, prev[2], li))
            prev = (cu["z"], n, li)

    def terminal(ref, pins):
        outer = job.copper[0 if comps[ref]["side"] == "top" else -1]["name"]
        names = [c["name"] for c in layers]
        side_li = names.index(outer) if outer in names else None
        n = node()
        tied = 0
        pads = []
        if side_li is not None:
            syms, feats, units = job.layer_features(outer)
            for fi in job.fids(args.net).get(outer, ()):
                if feats[fi][0] == "P":
                    pads.append(feature_geom(syms, feats[fi], units, job)[0])
        for pname, px, py, _ in pins:
            th = [
                hi
                for hi, hh in enumerate(holes)
                if math.hypot(hh[0] - px, hh[1] - py) < 0.1
            ]
            if th:  # through-hole pin: the soldered pin ties the whole barrel
                for li in range(len(layers)):
                    if (th[0], li) in barrel:
                        add(np.array([barrel[(th[0], li)]]), np.array([n]), BIG)
                        tied += 1
                continue
            if side_li is None:
                continue
            pad = next(
                (p for p in pads if p is not None and p.contains(Point(px, py))), None
            )
            shape = pad if pad is not None else Point(px, py).buffer(0.25)
            m = shapely.contains_xy(shape, XX, YY) & masks[side_li]
            c = idx[side_li][m]
            add(c, np.full(len(c), n), BIG)
            tied += len(c)
        if not tied:
            sys.exit(f"terminal {ref}: no copper of {args.net} found at its pins")
        return n

    src = terminal(terms[0][1], terms[0][2])
    dst = [terminal(ref, pins) for _, ref, pins in terms[1:]]
    NT = nxt[0]
    G = sp.csr_matrix(
        (np.concatenate(V), (np.concatenate(R), np.concatenate(C))), shape=(NT, NT)
    )
    G = G + sp.identity(NT) * 1e-9  # keeps floating islands from making G singular
    keep = np.ones(NT, bool)
    keep[src] = False
    mp = -np.ones(NT, int)
    mp[keep] = np.arange(keep.sum())
    print(f"  solving {keep.sum()} nodes ...", file=sys.stderr)
    lu = spl.splu(G[keep][:, keep].tocsc())
    results = []
    Vref = None
    for (_, ref, pins), n in zip(terms[1:], dst):
        rhs = np.zeros(keep.sum())
        rhs[mp[n]] = 1.0
        v = lu.solve(rhs)
        r = v[mp[n]]
        results.append({"to": ref, "pins": [p[0] for p in pins], "r_mohm": r * 1e3})
        if Vref is None:
            Vref = np.zeros(NT)
            Vref[keep] = v
    # potentials for 1 A flowing src -> first target: V = Vref[first] - Vref
    Vp = Vref[dst[0]] - Vref
    out = {
        "net": args.net,
        "source": {"ref": terms[0][1], "pins": [p[0] for p in terms[0][2]]},
        "grid_mm": h,
        "plating_mm": plate,
        "rho_ohm_mm": rho,
        "stackup": [
            {
                "layer": c["name"],
                "t_um": round(c["t"] * 1000, 2),
                "z_mm": round(c["z"], 4),
            }
            for c in job.copper
        ],
        "results": results,
    }
    print(
        f"\n{args.net}: R from {terms[0][1]} (pins {','.join(out['source']['pins'])}), 20 C, grid {h * 1000:.0f} um"
    )
    for rr in results:
        t = ", ".join(
            f"{T} C {rr['r_mohm'] * (1 + ALPHA_CU * (T - 20)):.2f}" for T in args.temps
        )
        print(
            f"  -> {rr['to']:<8} pins {','.join(rr['pins']):<10} {rr['r_mohm']:8.2f} mOhm   ({t})"
        )

    # vias carrying the reference current
    pin_holes = {
        hi
        for _, _, pins in terms
        for p in pins
        for hi, hh in enumerate(holes)
        if math.hypot(hh[0] - p[1], hh[1] - p[2]) < 0.1
    }
    vrep = []
    for hi, na, nb, r, area, la, lb in vias:
        if hi in pin_holes:  # barrel inside a soldered terminal pin
            continue
        i = (Vp[na] - Vp[nb]) / r
        if abs(i) > 0.01:
            x, y, d, *_ = holes[hi]
            vrep.append(
                {
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "drill_mm": d,
                    "between": [layers[la]["name"], layers[lb]["name"]],
                    "current_per_A": round(abs(i), 3),
                    "r_mohm": round(r * 1e3, 3),
                    "ipc2221_A_10C": round(ipc2221(area, 10, True), 2),
                    "ipc2221_A_20C": round(ipc2221(area, 20, True), 2),
                }
            )
    print(
        f"\nbarrel segments carrying >1% of current ({terms[0][1]} -> {terms[1][1]}):"
    )
    vrep.sort(key=lambda v: -v["current_per_A"])
    for v in vrep[:12]:
        print(
            f"  ({v['x']:.2f},{v['y']:.2f}) d={v['drill_mm']:.3f} {v['between'][0]}->{v['between'][1]}: "
            f"{v['current_per_A']:.2f} A/A, {v['r_mohm']:.2f} mOhm, IPC-2221 {v['ipc2221_A_10C']} A @+10C"
        )
    out["barrels"] = vrep

    # hotspots: local maxima of linear current density, per layer
    hot = []
    exc_all = np.zeros(XX.shape, bool)
    for x, y, d, *_ in holes:
        exc_all |= (XX - x) ** 2 + (YY - y) ** 2 <= (d / 2 + 2 * h) ** 2
    fields = []
    for li, cu in enumerate(layers):
        ids, m = idx[li], masks[li]
        Vg = np.where(m, Vp[np.maximum(ids, 0)], np.nan)
        gx = np.zeros_like(Vg)
        gy = np.zeros_like(Vg)
        gx[:, 1:] = np.nan_to_num((Vg[:, 1:] - Vg[:, :-1]) / h)
        gy[1:, :] = np.nan_to_num((Vg[1:, :] - Vg[:-1, :]) / h)
        K = np.hypot(gx, gy) * cu["t"] / rho  # A per mm of width, per A
        K[~m] = np.nan
        fields.append((cu["name"], Vg, K))
        Kc = np.where(exc_all, np.nan, K)
        for _ in range(args.hotspots):
            if not np.isfinite(Kc).any() or np.nanmax(Kc) < 0.05:
                break
            i = int(np.nanargmax(Kc))
            k, x, y = Kc.flat[i], XX.flat[i], YY.flat[i]
            w = 1 / k
            area = w * cu["t"]
            hot.append(
                dict(
                    layer=cu["name"],
                    x=round(x, 2),
                    y=round(y, 2),
                    A_per_mm=round(k, 3),
                    eff_width_mm=round(w, 3),
                    area_mil2=round(area * MIL2_PER_MM2, 1),
                    **{
                        f"ipc2221_A_{T}C": round(ipc2221(area, T, cu["outer"]), 2)
                        for T in (10, 20, 30)
                    },
                )
            )
            Kc[(XX - x) ** 2 + (YY - y) ** 2 <= args.hotspot_sep**2] = np.nan
    # keep only spots that carry a meaningful share of the peak density
    kmax = max((d["A_per_mm"] for d in hot), default=0)
    hot = [d for d in hot if d["A_per_mm"] >= args.hotspot_frac * kmax]
    hot.sort(key=lambda d: d["ipc2221_A_10C"])
    print(
        "\ncurrent-density hotspots (holes excluded), weakest first; eff width = 1/(A per mm):"
    )
    for d in hot:
        print(
            f"  {d['layer']:<14} ({d['x']:7.2f},{d['y']:7.2f})  {d['A_per_mm']:5.2f} A/mm  w_eff {d['eff_width_mm']:.3f} mm  "
            f"{d['area_mil2']:6.1f} mil2  IPC-2221 {d['ipc2221_A_10C']}/{d['ipc2221_A_20C']}/{d['ipc2221_A_30C']} A @+10/20/30C"
        )
    out["hotspots"] = hot

    if args.out:
        od = Path(args.out)
        od.mkdir(parents=True, exist_ok=True)
        (od / "result.json").write_text(json.dumps(out, indent=1))
        plot(od, fields, xs, ys, holes, terms, comps)
        print(f"\nwrote {od}/result.json and plots", file=sys.stderr)


def plot(od, fields, xs, ys, holes, terms, comps):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    ext = [xs[0], xs[-1], ys[0], ys[-1]]
    aspect = (ys[-1] - ys[0]) / (xs[-1] - xs[0])
    for name, Vg, K in fields:
        if not np.isfinite(K).any() or np.nanmax(K) < 1e-3:
            continue
        fig, axs = plt.subplots(2, 1, figsize=(14, 2 * 14 * aspect + 1.5))
        im = axs[0].imshow(Vg * 1e3, origin="lower", extent=ext, cmap="viridis")
        fig.colorbar(im, ax=axs[0], label="mV per A")
        axs[0].set_title(f"{name}: potential")
        im = axs[1].imshow(
            K,
            origin="lower",
            extent=ext,
            cmap="inferno",
            norm=LogNorm(0.01, max(1, np.nanmax(K))),
        )
        fig.colorbar(im, ax=axs[1], label="A per mm width, per A")
        axs[1].set_title(f"{name}: linear current density")
        for a in axs:
            for x, y, d, *_ in holes:
                a.add_patch(
                    plt.Circle((x, y), max(d / 2, 0.15), fill=False, ec="c", lw=0.6)
                )
            for role, ref, pins in terms:
                a.plot(
                    [p[1] for p in pins],
                    [p[2] for p in pins],
                    "c*" if role == "to" else "c^",
                    ms=10,
                )
                a.annotate(
                    ref,
                    (pins[0][1], pins[0][2]),
                    color="c",
                    fontsize=9,
                    xytext=(4, 4),
                    textcoords="offset points",
                )
        fig.tight_layout()
        fig.savefig(od / f"{name}.png", dpi=80)
        plt.close(fig)


# --------------------------------------------------------------------------
# info / pins


def info(args):
    job = Job(args.odb, args.step)
    print(f"job {job.root}  step {job.step}")
    print(f"\ncopper stack (computed thickness {job.thickness:.3f} mm):")
    for c in job.copper:
        print(
            f"  {c['name']:<16} {c['type']:<13} {'NEG' if c['negative'] else 'pos'}  t={c['t'] * 1000:6.2f} um  z={c['z']:.3f} mm"
        )
    print("drill layers:", [d[0] for d in job.drill_layers()])
    nets = job.nets
    if args.grep:
        nets = [n for n in nets if re.search(args.grep, n, re.IGNORECASE)]
        print(f"\nnets matching {args.grep!r}:")
        for n in nets:
            f = job.net_fids[job.nets.index(n)]
            cnt = Counter({k: len(v) for k, v in f.items()})
            print(f"  {n:<24} " + " ".join(f"{k}:{v}" for k, v in cnt.most_common(8)))
    else:
        print(f"\n{len(nets)} nets; use --grep to list")


def pins(args):
    job = Job(args.odb, args.step)
    comps = job.components()
    for ref in args.refs:
        c = comps.get(ref)
        if not c:
            print(f"{ref}: not found")
            continue
        print(
            f"{ref} ({c['side']}, {c['part']}, {c['props'].get('MFR_PN', '')}) at ({c['x']:.3f}, {c['y']:.3f})"
        )
        for p in sorted(c["pins"], key=lambda p: (len(p[0]), p[0])):
            print(f"  pin {p[0]:<4} ({p[1]:9.3f}, {p[2]:9.3f})  {p[3]}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("info", "pins", "solve"):
        p = sub.add_parser(name)
        p.add_argument("odb", help="ODB++ .tgz/.tar or extracted directory")
        p.add_argument("--step", help="step name (default: first)")
        if name == "info":
            p.add_argument("--grep", help="regex to list matching nets")
        if name == "pins":
            p.add_argument("refs", nargs="+")
        if name == "solve":
            p.add_argument("--net", required=True)
            p.add_argument("--from", dest="src", required=True, help="REFDES[:pin,pin]")
            p.add_argument(
                "--to",
                action="append",
                required=True,
                help="REFDES[:pin,pin]; repeatable",
            )
            p.add_argument(
                "--grid", type=float, default=0.05, help="cell size mm (default 0.05)"
            )
            p.add_argument(
                "--plating",
                type=float,
                default=0.020,
                help="barrel plating mm (default 0.020)",
            )
            p.add_argument("--cu", nargs="*", help="override copper: LAYER=um ...")
            p.add_argument(
                "--bbox",
                type=float,
                nargs=4,
                metavar=("X0", "Y0", "X1", "Y1"),
                help="crop region mm",
            )
            p.add_argument(
                "--temps",
                type=float,
                nargs="*",
                default=[60.0],
                help="also report R at these C",
            )
            p.add_argument("--hotspots", type=int, default=6, help="hotspots per layer")
            p.add_argument(
                "--hotspot-sep",
                type=float,
                default=2.0,
                help="min spacing between hotspots, mm",
            )
            p.add_argument(
                "--hotspot-frac",
                type=float,
                default=0.3,
                help="drop hotspots below this fraction of the peak density",
            )
            p.add_argument("--out", help="directory for result.json and per-layer PNGs")
    args = ap.parse_args()
    {"info": info, "pins": pins, "solve": solve}[args.cmd](args)


if __name__ == "__main__":
    main()
