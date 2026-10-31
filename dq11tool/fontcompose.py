"""Accented Latin letters built from a font's own glyphs (é = e + acute ...).

Only the two message fonts get real western glyphs (fontimport); the menu and small fonts
(3D12, 2D9, 2D14SH) have no Latin font of their size to take glyphs from. Their accented
letters are composed instead: the base letter of the same font plus a small drawn accent,
so they keep the font's style and advance widths. Capitals need room above the cell, so the
whole sheet is "lifted": every glyph moves down a few rows and the baseline with it (the game
positions glyphs by the baseline, so text does not move).

Outlined fonts (2D14SH) get an outline around the accent like the font's own strokes.
"""
from __future__ import annotations

import unicodedata

from .fontimport import Font, _ink_box, _rewrite

ACCENTS = {     # 2-row shapes, '#' = ink
    "grave": ["#.", ".#"],
    "acute": [".#", "#."],
    "circ": [".#.", "#.#"],
    "tilde": [".#.#", "#.#."],
    "diaer": ["#.#"],
    "ring": [".#.", "#.#", ".#."],
}
_MARK = {"̀": "grave", "́": "acute", "̂": "circ", "̃": "tilde",
         "̈": "diaer", "̊": "ring", "̧": "cedilla"}
CEDILLA = [".#", "##"]

# what the name keyboard and translations can use
CHARS = ("ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜÝŸŒ"
         "àáâãäåæçèéêëìíîïñòóôõöøùúûüýÿœ¡¿")
_LIGATURES = {"Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe"}
_SLASHED = {"Ø": "O", "ø": "o"}
_TURNED = {"¡": "!", "¿": "?"}

SYMBOLS = "♥"     # drawn, not composed: a heart (the game fonts only have the note and the star)

VERSION = 2             # bump when the composed glyphs change (font cache key)
CORE = 12               # pixel value from which a pixel counts as the glyph's stroke


def _rows(glyph: list[bytes]) -> list[list[int]]:
    return [list(r) for r in glyph]


def _core_box(px: list[list[int]]):
    ys = [y for y, r in enumerate(px) if any(v >= CORE for v in r)]
    if not ys:
        return None
    xs = [x for x in range(len(px[0])) if any(px[y][x] >= CORE for y in ys)]
    return xs[0], ys[0], xs[-1] + 1, ys[-1] + 1


def _is_outlined(f: Font) -> bool:
    """Fonts with a drawn outline/shadow: most ink pixels are mid-tone."""
    px = [v for r in f.glyph(f.cmap[ord("e")]) for v in r if v]
    return sum(1 for v in px if 4 <= v <= 11) > len(px) * 0.55   # antialiased fonts: ~40%


class _Canvas:
    """Glyph pixels on a grid with room above (negative rows) and below the cell."""

    def __init__(self, f: Font, gi: int, pad_top: int, pad_bottom: int, width: int):
        self.top = pad_top
        self.h = pad_top + f.ch + pad_bottom
        self.w = width
        self.px = [[0] * width for _ in range(self.h)]
        for y, r in enumerate(f.glyph(gi)):
            for x, v in enumerate(r[:width]):
                self.px[y + pad_top][x] = v

    def put(self, shape: list[str], x0: int, y0: int, value: int = 15):
        """y0 in cell coordinates (0 = old cell top)."""
        for dy, line in enumerate(shape):
            for dx, c in enumerate(line):
                if c == "#":
                    y, x = y0 + self.top + dy, x0 + dx
                    if 0 <= y < self.h and 0 <= x < self.w:
                        self.px[y][x] = max(self.px[y][x], value)


def _outline(c: _Canvas, before: list[list[int]]):
    """Give newly drawn stroke pixels the font's outline: 8 next to the stroke, 3 one further."""
    new = [(y, x) for y in range(c.h) for x in range(c.w) if c.px[y][x] >= CORE and before[y][x] < CORE]
    for ring, val in ((1, 8), (2, 3)):
        for y, x in new:
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < c.h and 0 <= xx < c.w and c.px[yy][xx] < val:
                        c.px[yy][xx] = val


def _squash(cv: "_Canvas", b, k: int):
    """Make the letter in box b k rows shorter by dropping rows that repeat the row above
    (straight stems), keeping its bottom on the baseline. Returns the new box."""
    l, t, r, btm = b
    for _ in range(k):
        rows = range(cv.top + t + 1, cv.top + btm - 1)
        dup = [y for y in rows if cv.px[y][l:r] == cv.px[y - 1][l:r]]
        mid = cv.top + (t + btm) // 2
        y = min(dup, key=lambda y: abs(y - mid)) if dup else mid
        for yy in range(y, cv.top + t - 2, -1):       # move everything above down by one
            cv.px[yy] = cv.px[yy - 1][:] if yy - 1 >= 0 else [0] * cv.w
        t += 1
    return l, t, r, btm


def _is_aa(f: Font) -> bool:
    """Antialiased glyphs (mid-tone pixels along the strokes) rather than hard pixel art."""
    px = [v for r in f.glyph(f.cmap[ord("e")]) for v in r if v]
    return sum(1 for v in px if 3 <= v <= 12) > len(px) * 0.12


def _heart(f: Font, cw: int, lift: int, below: int, outlined: bool, cap) -> tuple:
    """A filled heart as tall as the capitals, standing on the baseline, in the font's style."""
    l, t, r, b = cap
    hh = max(5, b - t)
    h = hh - (1 if outlined else 0)
    w = min(round(h * 1.12), cw - (4 if outlined else 2))
    h = max(h, 5)
    aa = _is_aa(f)
    grid = 4
    ox = 1 if outlined else 0
    cv = _Canvas(f, f.cmap[ord("e")], lift, below, cw)
    cv.px = [[0] * cw for _ in range(cv.h)]
    y_top = cv.top + b - h - (1 if outlined else 0)
    for py in range(h):
        for px in range(w):
            hit = 0
            for sy in range(grid):
                for sx in range(grid):
                    x = ((px + (sx + 0.5) / grid) / w) * 2.3 - 1.15
                    y = 1.25 - ((py + (sy + 0.5) / grid) / h) * 2.3
                    if (x * x + y * y - 1) ** 3 - x * x * y ** 3 <= 0:
                        hit += 1
            cov = hit / (grid * grid)
            v = round(cov * 15) if aa else (15 if cov >= 0.5 else 0)
            if v and 0 <= y_top + py < cv.h and ox + px < cw:
                cv.px[y_top + py][ox + px] = v
    before = [[0] * cw for _ in range(cv.h)]
    if outlined:
        _outline(cv, before)
    gw = min(cw, w + (4 if outlined else 0))
    return [bytes(r) for r in cv.px], (0, gw, gw + 1)


def compose(data: bytes, chars: str = CHARS + SYMBOLS) -> bytes:
    """Font with the missing characters of `chars` composed from its own glyphs."""
    f = Font(data)
    if f.magic != b"FFNT" or f.fmt not in (10, 11) or ord("e") not in f.cmap:
        return data
    todo = [c for c in chars if ord(c) not in f.cmap]
    if not todo:
        return data
    outlined = _is_outlined(f)
    halo = 2 if outlined else 0
    gap = 1

    def box(ch):
        gi = f.cmap.get(ord(ch))
        return _core_box(_rows(f.glyph(gi))) if gi is not None else None

    # ---- plan: base glyph + accent placement, in old cell coordinates
    plans = {}
    for c in todo:
        if c in SYMBOLS:
            if ord("H") in f.cmap and f.glyph_count > 1000:     # not the tight credits font
                plans[c] = ("heart", None, None)
            continue
        if c in _LIGATURES or c in _SLASHED or c in _TURNED:
            base = (_LIGATURES.get(c) or _SLASHED.get(c) or _TURNED.get(c))
            if all(ord(b) in f.cmap for b in base):
                plans[c] = ("special", base, None)
            continue
        d = unicodedata.normalize("NFD", c)
        base, mark = d[0], _MARK.get(d[1:2])
        if mark is None or ord(base) not in f.cmap:
            continue
        if base == "i":
            base = "ı"      # dotless: drawn from 'i' with the dot removed
        plans[c] = ("accent", base, mark)
    if not plans:
        return data

    def base_box(base: str):
        b = box("i" if base == "ı" else base)
        if base == "ı":                     # top of the stem = x-height
            b = (b[0], box("x")[1] if box("x") else b[1], b[2], b[3])
        return b

    def accent_top(base: str, mark: str) -> int:
        # the outline (halo) may be clipped at the cell top, only the strokes must fit
        return base_box(base)[1] - gap - len(ACCENTS[mark])

    def adv(ch):
        return f.width(f.cmap[ord(ch)])

    def capacity(cw, ch):
        return (f.sw // (cw + 1)) * (f.sh // (ch + 1)) * f.nsheets

    # ligatures need a wider cell; keep them only if the sheet has room for that
    lig_w = f.cw
    for c, (kind, base, _) in plans.items():
        if kind == "special" and len(base) == 2:
            a_, b_ = box(base[0]), box(base[1])
            lig_w = max(lig_w, max(0, adv(base[0])[0]) + (a_[2] - a_[0]) + (b_[2] - b_[0]) - 1 + halo)

    want_lift = max([0] + [-accent_top(base, m) for kind, base, m in plans.values()
                           if kind == "accent" and m != "cedilla"])
    want_below = max([0] + [box(base)[3] + len(CEDILLA) - f.ch for kind, base, m in plans.values()
                            if kind == "accent" and m == "cedilla"])
    BLANKS = 11                            # fontimport.add_blank still needs room after us

    def find_cell(n_new):
        for cw in ([lig_w, f.cw] if lig_w > f.cw else [f.cw]):
            total = f.glyph_count + n_new + BLANKS
            extra = 0                           # rows the sheet can still give
            while extra < want_lift + want_below and capacity(cw, f.ch + extra + 1) >= total:
                extra += 1
            if capacity(cw, f.ch + extra) < total:
                continue
            if cw > f.cw and extra < min(want_lift + want_below, 2):
                continue                        # rather lose the ligatures than the accents
            below = min(want_below, max(0, extra - want_lift))     # accents above come first
            lift = extra - below
            ch = f.ch + extra
            return lift, below, (cw, ch, f.sw // (cw + 1), f.sh // (ch + 1))
        return None

    found = find_cell(len(plans))
    if found is None and any(k == "heart" for k, _, _ in plans.values()):
        plans = {c: v for c, v in plans.items() if v[0] != "heart"}     # the heart is optional
        found = find_cell(len(plans))
    if found is None:
        return data
    lift, below, cell = found
    cw = cell[0]

    # ---- draw
    glyphs = {}
    for c, (kind, base, mark) in plans.items():
        if kind == "heart":
            glyphs[ord(c)] = _heart(f, cw, lift, max(0, below), outlined, box("H"))
            continue
        src = "i" if base == "ı" else base[0]
        cv = _Canvas(f, f.cmap[ord(src)], lift, max(0, below), cw)
        before = [r[:] for r in cv.px]
        left, gw, advance = adv(src)
        b = box(src)
        if base == "ı":                         # remove the dot of i
            xt = box("x")[1] if box("x") else b[1]
            for y in range(0, cv.top + xt - (1 if outlined else 0)):
                cv.px[y] = [0] * cw
            b = (b[0], xt, b[2], b[3])
        if kind == "accent" and mark == "cedilla":
            cx = (b[0] + b[2]) // 2 - 1
            room = f.ch + below - b[3]
            cv.put(CEDILLA if room >= len(CEDILLA) else CEDILLA[-1:], cx, b[3] if room else b[3] - 1)
        elif kind == "accent":
            shape = ACCENTS[mark]
            need = -lift - (b[1] - gap - len(shape))
            if need > 0 and gap:            # first: let a capital's accent sit on the letter
                need -= 1
                y_gap = 0
            else:
                y_gap = gap
            if need > 0:
                b = _squash(cv, b, need)
            sw = len(shape[0])
            if mark == "diaer":                 # two dots over the letter's stems
                inner = max(1, (b[2] - b[0]) - 3 if b[2] - b[0] > 4 else 1)
                shape = ["#" + "." * inner + "#"]
                sw = len(shape[0])
            x0 = b[0] + ((b[2] - b[0]) - sw + 1) // 2
            y0 = max(-lift, b[1] - y_gap - len(shape))
            cv.put(shape, x0, y0)
        elif kind == "special" and base in _SLASHED.values():
            # diagonal from bottom-left to top-right, one pixel past the letter
            x1, y1, x2, y2 = b[0] - 1, b[3], b[2], b[1] - 1
            n = max(x2 - x1, y1 - y2)
            for i in range(n + 1):
                x = x1 + round(i * (x2 - x1) / n)
                y = y1 - round(i * (y1 - y2) / n)
                cv.put(["#"], x, y)
        elif kind == "special" and len(base) == 1:        # turned ! ?
            gb = _ink_box(f.glyph(f.cmap[ord(src)]))
            l, t_, r, btm = gb
            old = [row[:] for row in cv.px]
            for y in range(t_, btm):
                for x in range(l, r):
                    cv.px[y + cv.top][x] = old[cv.top + (t_ + btm - 1 - y)][l + r - 1 - x]
            # hang below the baseline like ¡ ¿ do: shift down by the descender room
            drop = min(max(0, below) + f.ch - btm, (box("p")[3] - box("o")[3]) if box("p") and box("o") else 0)
            if drop > 0:
                cv.px = [[0] * cw] * drop + cv.px[:-drop]
        elif kind == "special":                          # ligature: second letter overlaps by 1
            gi2 = f.cmap[ord(base[1])]
            b2 = box(base[1])
            g2 = f.glyph(gi2)
            # overlap by one column, more (sharing strokes) when the cell is too narrow
            w1, w2 = b[2] - b[0], b2[2] - b2[0]
            ov = max(1, max(0, left) + b[0] + w1 + w2 + halo - cw)
            if ov > w2 // 2:
                continue
            dx = b[2] - ov - b2[0]
            for y, row in enumerate(g2):
                for x, v in enumerate(row):
                    xx = x + dx
                    if v and 0 <= xx < cw:
                        cv.px[y + cv.top][xx] = max(cv.px[y + cv.top][xx], v)
            # pixel column x of the cell is drawn at pen + left, so the second letter's own
            # pen position is pen + left + dx - left2
            l2, _, adv2 = adv(base[1])
            advance = left + dx - l2 + adv2
        if outlined:
            _outline(cv, before)
        ink = _ink_box([bytes(r) for r in cv.px])
        if ink:
            gw = max(gw, min(cw, ink[2]) - max(0, left))
        glyphs[ord(c)] = ([bytes(r) for r in cv.px], (left, min(gw, cw), max(advance, gw + left)))

    out, _, _ = _rewrite(data, f, glyphs, cell, lift)
    return out
