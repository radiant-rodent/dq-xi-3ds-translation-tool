"""Narrow copies of the Latin glyphs inside each game font, for text that is wider than its pane.

Text that did not fit its pane used to be drawn at a fractional x scale (layout font size,
runtime fit). The 3DS samples the glyph sheet bilinearly, so every letter came out blurred
and smeared, worst with the pixel fonts of the 2D screens (2D9, 2D14; user 2026-10-22).

Instead every font gets LEVELS narrower versions of its Latin letters, condensed once at
build time on the pixel grid: the gaps between letters shrink first (one empty column always
stays), then each glyph loses the columns whose removal changes it least - a column merged
into its neighbour (two-pixel stems become one, round letters lose a middle column); a
removal that would join two strokes (the counters of m, w, e) is avoided. Nothing is
resampled, so the narrow letters are as sharp as the normal ones.

Level L (1..LEVELS) of character c is the code CODE_BASE + L * 0x100 + c (U+E200..U+E5FF,
private use; the game's own text has none). The glyphs take over the cells of kanji that no
string of the game uses (the fonts keep their size, so the game needs no extra memory) and
the free cells at the end of the sheet. Space keeps its code: the runtime word wrap breaks
lines at U+0020 only.

The runtime fit (autofit.py) maps a string to the first level whose width fits its pane;
the exporter does the same for text it knows (narrow()).
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

from .fontimport import Font, _rewrite

VERSION = 3                 # bump when the generated glyphs change (font cache key)
LEVELS = (0.9, 0.8, 0.7, 0.6)  # target advance of each level, times the normal one
CODE_BASE = 0xE100
# characters with narrow versions: printable ASCII except space, and the Latin-1 letters
# a translation can use (fontcompose.CHARS minus the rare ones)
CHARS = tuple(range(0x21, 0x7F)) + tuple(map(ord, "¡¿ÀÁÂÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÖØÙÚÛÜßàáâäåæçèéêëìíîïñòóôöøùúûü"))
FONTS = ("2D9", "2D14", "2D14SH", "3D12", "3D14")   # credit2D: the credits are never squeezed
MIN_GAP = 1                 # empty columns kept between two letters
SHORT = 3                   # px: horizontal strokes this short are arms / serifs (short_runs)
JOIN_COST = 6.0             # cost of a removal per pixel row in which two strokes would touch
MAX_COST = 8.0              # a glyph is not condensed further once every removal costs more


def code(c: int, level: int) -> int:
    return CODE_BASE + level * 0x100 + c


def bitmap() -> bytes:
    """32 bytes, bit c set for every character with narrow versions (autofit's table)."""
    out = bytearray(32)
    for c in CHARS:
        out[c >> 3] |= 1 << (c & 7)
    return bytes(out)


def narrow(text: str, level: int) -> str:
    """`text` drawn with level `level` (0 = normal) glyphs; the character after a {v:..}
    control (U+0004) is its argument and stays."""
    out, skip = [], False
    chars = set(CHARS)
    for ch in plain(text):
        o = ord(ch)
        if not skip and level and o in chars:
            ch = chr(code(o, level))
        skip = o == 4 and not skip
        out.append(ch)
    return "".join(out)


def plain(text: str) -> str:
    """Narrow codes back to the normal characters."""
    return "".join(chr(ord(ch) & 0xFF) if CODE_BASE + 0x100 <= ord(ch) < code(0, len(LEVELS) + 1) else ch
                   for ch in text)


# ---------------------------------------------------------------------- condensing

class _Style:
    def __init__(self, f: Font):
        from .fontcompose import _is_outlined
        self.outlined = _is_outlined(f)
        # stroke pixels: the core of an outlined glyph (its ring is mid-tone)
        self.ink = 0.75 if self.outlined else 0.45
        # pixel fonts (few mid-tone pixels) merge with max; antialiased ones would turn bold
        px = [v for r in f.glyph(f.cmap[ord("e")]) for v in r if v]
        self.pixel = not self.outlined and sum(1 for v in px if 3 <= v <= 12) < len(px) * 0.25
        # empty columns every letter has before its ink (2D9 / 2D14: 1): that much of the gap
        # between two letters comes from the next one, the right side may lose it
        starts = []
        for c in range(0x41, 0x7B):
            gi = f.cmap.get(c)
            if gi is None or not chr(c).isalpha():
                continue
            l, gw, _ = f.width(gi)
            xs = [x for x in range(gw) if any(r[x] >= 6 for r in f.glyph(gi))]
            if xs:
                starts.append(l + xs[0])
        self.min_left = max(0, min(starts, default=0))

    def merge(self, a, b):
        if self.pixel or self.outlined:
            return np.maximum(a, b)
        return np.maximum(np.maximum(a, b) * 0.85, (a + b) / 2)

    def runs(self, g):
        b = g >= self.ink
        return (b[:, 1:] & ~b[:, :-1]).sum(1) + b[:, 0]

    def short_runs(self, g):
        """Per row: total length of its strokes 2..SHORT px long - the arm of f, r, t, a serif,
        the bar of 4 - which a removal must not shorten (carved, "of" read "ot")."""
        b = g >= self.ink
        out = np.zeros(g.shape[0])
        for y, row in enumerate(b):
            n = 0
            for v in list(row) + [False]:
                if v:
                    n += 1
                else:
                    if 2 <= n <= SHORT:
                        out[y] += n
                    n = 0
        return out

    def thin_gaps(self, g):
        """Per row: one-pixel gaps between two strokes (an H whose counter is about to close)."""
        b = g >= self.ink
        return (b[:, :-2] & ~b[:, 1:-1] & b[:, 2:]).sum(1)


def _carve_one(g, st: _Style):
    """(cost, glyph) of the cheapest removal of one column."""
    r0, t0, s0 = st.runs(g), st.thin_gaps(g), st.short_runs(g)
    best = None
    for i in range(g.shape[1]):
        for j in (i - 1, i + 1):
            if not 0 <= j < g.shape[1]:
                continue
            m = st.merge(g[:, i], g[:, j])
            h = g.copy()
            h[:, j] = m
            h = np.delete(h, i, axis=1)
            cost = np.abs(m - g[:, j]).sum() + 0.5 * np.abs(m - g[:, i]).sum()
            cost += JOIN_COST * np.maximum(r0 - st.runs(h), 0).sum()
            cost += JOIN_COST / 2 * np.maximum(st.thin_gaps(h) - t0, 0).sum()
            cost += JOIN_COST * np.maximum(s0 - st.short_runs(h), 0).sum()
            if best is None or cost < best[0]:
                best = (cost, h)
    return best


def _condense(g, left: int, adv: int, target: int, st: _Style, bearings: bool = True,
              carve: bool = True):
    """Glyph g (rows x glyph width, 0..1) narrowed towards advance `target`; bearings=False:
    the space around the glyph stays (2D14SH's letters are only one ring apart already).
    The left bearing always stays: lines of different levels start at the same x (left
    aligned menus: "Forge Items" began 1 px right of the narrowed lines, user 2026-10-22)."""
    right = adv - left - g.shape[1]
    d = adv - target
    t = max(0, min(d, right - max(0, MIN_GAP - st.min_left))) if bearings else 0
    right -= t
    d -= t
    while carve and d > 0 and g.shape[1] > 1:
        cost, h = _carve_one(g, st)
        if cost > MAX_COST:
            break
        g = h
        d -= 1
    return g, left, left + g.shape[1] + right


RING = 2                    # px of the outline 2D14SH draws around its letters
CORE = 9 / 15               # ... whose pixels are lighter than the letter's own


def _ring(core):
    """An outline like 2D14SH's around the core pixels: 8 next to the ink, 5, 2 further out
    (fontimport._outlined), RING columns added on both sides."""
    H, W = core.shape
    out = np.zeros((H, W + 2 * RING))
    out[:, RING:RING + W] = core
    ink = np.argwhere(core >= CORE)
    ring = np.zeros_like(out)
    for y, x in ink:
        for dy in range(-RING, RING + 1):
            for dx in range(-RING, RING + 1):
                yy, xx = y + dy, x + RING + dx
                if 0 <= yy < H:
                    d2 = dx * dx + dy * dy
                    ring[yy, xx] = max(ring[yy, xx], (8 if d2 <= 2 else 5 if d2 <= 4 else 2) / 15)
    return np.maximum(out, ring)


def levels(f: Font, c: int) -> list[tuple[list[bytes], tuple[int, int, int]]]:
    """[(cell rows, (left, glyph width, advance))] of character c for every level."""
    st = getattr(f, "_narrow_style", None) or _Style(f)
    f._narrow_style = st
    gi = f.cmap.get(c, f.cmap.get(ord("?")))
    left, gw, adv = f.width(gi)
    g = np.array([list(r[:gw]) for r in f.glyph(gi)], dtype=float) / 15.0
    if st.outlined and g.shape[1] > 2 * RING:
        # condense the letter alone, then draw its outline again: carved, the ring between two
        # letters got thinner and their strokes ran together ("Mimic" read "Mmic")
        core = np.where(g >= CORE, g, 0)[:, RING:-RING]
        cur = (core, left + RING, adv)
        cst = _Style.__new__(_Style)
        cst.outlined, cst.pixel, cst.ink = False, True, CORE
    else:
        cur, cst = (g, left, adv), st
    out = []
    for k in LEVELS:
        # digits and symbols only lose spacing: reshaped, "32/41" read "327A1"
        cur = _condense(*cur, max(1, round(adv * k)), cst, bearings=cst is st, carve=chr(c).isalpha())
        h, l, a = cur
        if cst is not st:
            h, l = _ring(h), l - RING
        rows = [bytes(int(round(v)) for v in (row * 15).clip(0, 15)).ljust(f.cw, b"\0")[:f.cw] for row in h]
        out.append((rows, (l, h.shape[1], a)))
    return out


def free_cells(f: Font, keep: set[int], shown: set[int] | None = None) -> list[tuple[int | None, int]]:
    """(code to drop or None, glyph index) of cells the narrow glyphs may take: the free
    cells after the last glyph, then kanji that no string of the game has (`keep`: every
    character of the Japanese text and the translations), then kanji the exported game does
    not show (`shown`: the characters of the strings it writes - 2D14SH has hardly a kanji
    that the Japanese text does not use)."""
    cap = f.cols * f.rows * f.nsheets
    cells = [(None, gi) for gi in range(f.glyph_count, cap)]
    used = {}
    for ch, gi in f.cmap.items():
        used.setdefault(gi, []).append(ch)
    kanji = [(ch, gi) for ch, gi in sorted(f.cmap.items(), key=lambda kv: -kv[0])
             if 0x4E00 <= ch <= 0x9FFF and len(used[gi]) == 1]
    cells += [(ch, gi) for ch, gi in kanji if ch not in keep]
    if shown is not None:
        cells += [(ch, gi) for ch, gi in kanji if ch in keep and ch not in shown]
    return cells


def add(data: bytes, keep: set[int], shown: set[int] | None = None) -> bytes:
    """The font with LEVELS narrow versions of CHARS, in cells of unused kanji (free_cells)."""
    f = Font(data)
    if f.magic != b"FFNT" or f.fmt not in (10, 11):
        return data
    need = len(CHARS) * len(LEVELS)
    cells = free_cells(f, keep, shown)
    if len(cells) < need:
        raise ValueError(f"narrow glyphs: {need} cells needed, only {len(cells)} free")
    glyphs, reuse, drop = {}, {}, set()
    it = iter(cells)
    for c in CHARS:
        for lvl, (rows, w) in enumerate(levels(f, c), 1):
            ch, gi = next(it)
            glyphs[code(c, lvl)] = (rows, w)
            if ch is not None:
                reuse[code(c, lvl)] = gi
                drop.add(ch)
    # the others take the free cells after the last glyph (_rewrite numbers them in code order)
    return _rewrite(data, f, glyphs, (f.cw, f.ch, f.cols, f.rows), 0, zero_kerning=False,
                    reuse=reuse, drop=drop)[0]


def build(name: str, data: bytes, keep: set[int], shown: set[int],
          cache_dir: str | None = None) -> bytes:
    """add() for the FONTS, cached by font data and the cells it may take."""
    if name not in FONTS:
        return data
    f = Font(data)
    need = len(CHARS) * len(LEVELS)
    cells = free_cells(f, keep, shown)[:need]
    key = hashlib.sha1(data + f"{VERSION}/{LEVELS}/{CHARS}/{cells}".encode()).hexdigest()[:16]
    cp = os.path.join(cache_dir, f"narrow_{name}_{key}.bffnt") if cache_dir else None
    if cp and os.path.isfile(cp):
        with open(cp, "rb") as fp:
            return fp.read()
    out = add(data, keep, shown)
    if cp:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cp, "wb") as fp:
            fp.write(out)
    return out


def savings(data: bytes) -> list[dict[int, int]]:
    """Per level 1..LEVELS: character -> pixels its narrow glyph is shorter than the normal one."""
    f = Font(data)
    out = [dict() for _ in LEVELS]
    for c in CHARS:
        adv = f.width(f.cmap.get(c, f.cmap.get(ord("?"))))[2]
        for i, (_, w) in enumerate(levels(f, c)):
            out[i][c] = adv - w[2]
    return out


def level_for(lines, width_of, saved: list[dict[int, int]], limit: float) -> tuple[int, list[str]]:
    """The lowest level at which every line is at most `limit` px wide (width_of: normal
    width of a line), and the lines still too wide at the last level."""
    lines = [ln for ln in lines if ln.strip()]
    widths = [(ln, width_of(ln)) for ln in lines]
    for lvl in range(len(LEVELS) + 1):
        cut = saved[lvl - 1] if lvl else {}
        over = [ln for ln, w in widths if w - sum(cut.get(ord(c), 0) for c in ln) > limit]
        if not over:
            return lvl, []
    return len(LEVELS), over
