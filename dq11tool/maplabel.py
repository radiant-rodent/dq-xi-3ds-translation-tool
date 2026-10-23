"""Place labels of the field maps (ui/map/*_2d.pack, *_3d.pack), one English name per label.

The Japanese map layouts spell a label with several text panes: one per character or a few
characters, set along a curve, stacked vertically, or as a parent pane with its continuation
in child panes ("デ" "ルカダ" "ー" "ル城下" "町"). Translated pane by pane, every fragment
became an abbreviation of its own ("He" "lio" "-" "Twn" "Twn"; the user saw "TwnT - Oct.").

plan() groups the panes of a layout into labels: panes closer than CLUSTER_GAP px are one
cluster; a cluster is read left to right (or top to bottom when it is taller than wide), and
when that is not a known label, in other orders, or split in two (two labels side by side).
Known labels and their English are data/map_labels.json (official PC names where there are
any). The first pane of a label gets the English name, centred on the cluster (an English
name is longer: centred, it stays on its building instead of running off to one side) and
LABEL_WIDTH wide at least (the runtime fit narrows longer names to that), the other panes
are emptied.
Clusters that are no known label keep their translation pane by pane (reported).
"""
from __future__ import annotations

import itertools
import json
import os
import struct
from collections import defaultdict

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "map_labels.json")
PANES = (b"pan1", b"pic1", b"txt1", b"wnd1", b"bnd1", b"prt1")
CLUSTER_GAP = 3.0
MERGE_GAP = 12.0
LABEL_WIDTH = 110.0
MAX_PERMUTE = 7
MAX_SPLIT = 10

_names: dict[str, str] | None = None


def names() -> dict[str, str]:
    global _names
    if _names is None:
        _names = json.load(open(DATA, encoding="utf-8"))
    return _names


def _panes(md: bytes):
    """Text panes: dict(i, name, text, rect=(left, top, right, bottom) with y up, parent
    position (absolute), height)."""
    from .bflyt import Bflyt
    lay = Bflyt(md)
    tp = {p.index: p for p in lay.text_panes()}
    out, stack, last, pos = [], [], None, {}
    for i, (m, off, sz) in enumerate(lay.sections):
        if m in PANES:
            tx, ty = struct.unpack_from("<ff", md, off + 0x2C)
            px, py = pos[stack[-1]] if stack else (0.0, 0.0)
            pos[i] = (px + tx, py + ty)
            last = i
            if m == b"txt1" and tp[i].text.strip():
                w, h = struct.unpack_from("<ff", md, off + 0x4C)
                ox, oy = md[off + 9] & 3, (md[off + 9] >> 2) & 3
                x, y = pos[i]
                x0 = x - w / 2 if ox == 0 else (x if ox == 1 else x - w)
                y0 = y + h / 2 if oy == 0 else (y if oy == 1 else y + h)
                out.append(dict(i=i, name=tp[i].name, text=tp[i].text, rect=(x0, y0, x0 + w, y0 - h),
                                ppos=(px, py), h=h))
        elif m == b"pas1":
            stack.append(last)
        elif m == b"pae1" and stack:
            stack.pop()
    return out


def _gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[3], b[3]) - min(a[1], b[1]))
    return max(dx, dy)


def _clusters(panes: list) -> list[list]:
    par = list(range(len(panes)))

    def root(k):
        while par[k] != k:
            par[k] = par[par[k]]
            k = par[k]
        return k
    for a, b in itertools.combinations(range(len(panes)), 2):
        if _gap(panes[a]["rect"], panes[b]["rect"]) <= CLUSTER_GAP:
            par[root(a)] = root(b)
    groups = defaultdict(list)
    for k, p in enumerate(panes):
        groups[root(k)].append(p)
    return list(groups.values())


def _centre(p):
    r = p["rect"]
    return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2


def _reading(c: list) -> list:
    xs = [_centre(p)[0] for p in c]
    ys = [_centre(p)[1] for p in c]
    if max(xs) - min(xs) >= max(ys) - min(ys):
        return sorted(c, key=lambda p: _centre(p)[0])
    return sorted(c, key=lambda p: -_centre(p)[1])


def _joined(c) -> str:
    return "".join(p["text"] for p in c).replace("\n", "")


def _resolve(c: list, known) -> list | None:
    """[(panes in reading order, Japanese label)] for a cluster, or None."""
    r = _reading(c)
    if _joined(r) in known:
        return [(r, _joined(r))]
    if len(c) <= MAX_PERMUTE:
        for perm in itertools.permutations(c):
            if _joined(perm) in known:
                return [(list(perm), _joined(perm))]
    if 2 <= len(c) <= MAX_SPLIT:
        for mask in range(1, 2 ** (len(c) - 1)):
            a = [p for k, p in enumerate(c) if mask >> k & 1]
            b = [p for k, p in enumerate(c) if not mask >> k & 1]
            ra = _resolve(a, known)
            rb = _resolve(b, known) if ra else None
            if ra and rb:
                return ra + rb
    return None


def plan(md: bytes):
    """(texts {section index: English}, set_float {pane: {offset: value}}, set_byte {...},
    set_width {pane: width}, unresolved [Japanese]) for one map layout."""
    known = names()
    texts, fl, by, wd, bad = {}, {}, {}, {}, []
    panes = _panes(md)
    count = defaultdict(int)
    for p in panes:
        count[p["name"]] += 1
    found, rest = [], []
    for c in _clusters(panes):
        res = _resolve(c, known)
        (found.append(res) if res else rest.append(c))
    # leftovers close to each other may be one label with a wider gap ("地下８" "階")
    def dist(c, o):
        return min(_gap(p["rect"], q["rect"]) for p in c for q in o)
    while rest:
        c = rest.pop()
        merged = False
        for o in sorted(rest, key=lambda o: dist(c, o)):
            if dist(c, o) > MERGE_GAP:
                break
            res = _resolve(c + o, known)
            if res:
                rest.remove(o)
                found.append(res)
                merged = True
                break
        if not merged:
            bad.append(_joined(_reading(c)))
    for res in found:
        for group, jp in res:
            first = group[0]
            x0 = min(p["rect"][0] for p in group)
            x1 = max(p["rect"][2] for p in group)
            y0 = max(p["rect"][1] for p in group)
            y1 = min(p["rect"][3] for p in group)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            texts[first["i"]] = known[jp]
            for p in group[1:]:
                texts[p["i"]] = ""
            if count[first["name"]] > 1:
                continue                       # edits go by pane name
            px, py = first["ppos"]
            fl[first["name"]] = {0x2C: cx - px, 0x30: cy - py}
            by[first["name"]] = {9: 0x00, 0x5C: 0x00, 0x5D: 2}     # centred pane, centred text
            wd[first["name"]] = max(LABEL_WIDTH, x1 - x0)
    return texts, fl, by, wd, bad
