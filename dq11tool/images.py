"""Japanese text that is part of layout images, redrawn in English.

Draconian Quest badges (timg/icon_bind.bflim, 64x128): four badges of 96x22 pixels, each a
64 px left piece (rows 0, 21, 42, 63) and a 32 px right piece from the bottom of the image.
The menus give badge 1 and 2 the same right piece (both end in ない), so badge 2 is pointed
at the unused fourth piece; then every badge has its own 96 px for the English text, which
is drawn with the bottom screen's pixel font.

"LEVEL UP!" (timg/result_lv1..7.bflim, one 32x32 letter each, layout result_lvup): the eight
letter panes get the letters L E V E L U P ! (prepared images in data/levelup) and new x
positions; the animation only moves them vertically.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from . import texture
from .fontimport import Font, _ink_box

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# packs with these images or their layouts (the pack entries are compressed, so the names
# cannot be searched for in the raw files)
PACKS = {"battle_3d.pack", "common_2way.pack", "common_3d.pack", "dive_dugeon_2way.pack",
         "dive_dugeon_btl_2way.pack", "poker_3d.pack", "roulette_3d.pack", "stamp_3d.pack"}

# ---------------------------------------------------------------------- Draconian Quest badges

BADGE_TEXTURE = "timg/icon_bind.bflim"
BADGE_TEXT = ("No Escape", "No Shops", "No Armour", "Bashful")   # 逃げられない 買物できない 防具そうびNG はずかしい
BADGE_ROWS = (0, 21, 42, 63)               # left pieces (64 x 22)
BADGE_RIGHT = ((32, 84), (0, 84), (0, 105), (32, 105))   # (x, y) of each badge's right piece (32 x 22)
BADGE_H = 22
# right pieces at x 0 start with a transparent column; their panes sit 1 px further left
SEAM_X = {0: 47.0, 32: 48.0}
TEXT_COLOR = (85, 17, 0, 255)
EDGE_COLOR = (255, 221, 85, 255)


def _text_mask(font: Font, text: str) -> np.ndarray:
    """1-bit text, emboldened by one pixel (the Japanese badge text has 2 px strokes)."""
    width = sum(font.width(font.cmap[ord(c)])[2] if ord(c) in font.cmap else 4 for c in text) + 2
    m = np.zeros((font.ch, width), bool)
    x = 0
    for c in text:
        gi = font.cmap.get(ord(c))
        if gi is None:
            x += 4
            continue
        left, gw, adv = font.width(gi)
        g = np.array([list(r[:gw]) for r in font.glyph(gi)]) > 7
        x0 = max(0, x + left)
        m[:, x0:x0 + g.shape[1]] |= g[:, :m.shape[1] - x0]
        x += adv
    m[:, 1:] |= m[:, :-1].copy()
    cols = np.where(m.any(0))[0]
    rows = np.where(m.any(1))[0]
    return m[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _draw_badge(badge: np.ndarray, font: Font, text: str):
    """badge: 22 x 96 RGBA, edited in place."""
    # the frame uses the text colour too: only look inside it
    old = np.zeros(badge.shape[:2], bool)
    old[3:-3, 8:-8] = np.all(badge[3:-3, 8:-8] == TEXT_COLOR, axis=2)
    ys, xs = np.where(old)
    if not len(xs):
        return
    t, b, l, r = ys.min() - 1, ys.max() + 2, xs.min() - 1, xs.max() + 2      # text + its outline
    # background: the clean columns left of the text, repeated over the text area
    src = badge[t:b, max(2, l - 4):l]
    reps = -(-(r - l) // src.shape[1])
    badge[t:b, l:r] = np.tile(src, (1, reps, 1))[:, :r - l]
    mask = _text_mask(font, text)
    h, w = mask.shape
    inner_l, inner_r = 7, badge.shape[1] - 7          # inside the end brackets
    if w + 2 > inner_r - inner_l:                       # too long: squeeze columns
        keep = np.linspace(0, w - 1, inner_r - inner_l - 2).round().astype(int)
        mask = mask[:, keep]
        h, w = mask.shape
    y0 = (t + b) // 2 - h // 2
    x0 = (inner_l + inner_r) // 2 - w // 2
    edge = np.zeros((h + 2, w + 2), bool)
    for dy in range(3):
        for dx in range(3):
            edge[dy:dy + h, dx:dx + w] |= mask
    badge[y0 - 1:y0 + h + 1, x0 - 1:x0 + w + 1][edge] = EDGE_COLOR
    badge[y0:y0 + h, x0:x0 + w][mask] = TEXT_COLOR


def redraw_badges(bflim: bytes, font_data: bytes) -> bytes:
    px = texture.decode(bflim).copy()                  # 128 x 64
    font = Font(font_data)
    for i, text in enumerate(BADGE_TEXT):
        y = BADGE_ROWS[i]
        rx, ry = BADGE_RIGHT[i]
        skip = 1 if rx == 0 else 0                     # the seam column
        badge = np.concatenate([px[y:y + BADGE_H, 0:64], px[ry:ry + BADGE_H, rx + skip:rx + 32]], 1)
        if i == 1:                                     # its own right piece is unused until now
            src = px[BADGE_RIGHT[0][1]:BADGE_RIGHT[0][1] + BADGE_H, 32 + skip:64]
            badge[:, 64:] = src
        _draw_badge(badge, font, text)
        px[y:y + BADGE_H, 0:64] = badge[:, :64]
        px[ry:ry + BADGE_H, rx + skip:rx + 32] = badge[:, 64:]
    return texture.encode(px, bflim)


def _textures(d: bytes, lay) -> list[str]:
    for m, off, sz in lay.sections:
        if m == b"txl1":
            n = struct.unpack_from("<H", d, off + 8)[0]
            out = []
            for i in range(n):
                o = struct.unpack_from("<I", d, off + 12 + 4 * i)[0]
                s = off + 12 + o
                out.append(d[s:d.index(b"\0", s)].decode())
            return out
    return []


def _materials(d: bytes, lay) -> list[int]:
    """File offsets of the materials (mat1)."""
    for m, off, sz in lay.sections:
        if m == b"mat1":
            n = struct.unpack_from("<H", d, off + 8)[0]
            return [off + o for o in struct.unpack_from(f"<{n}I", d, off + 12)]
    return []


def _pics(d: bytes, lay):
    """(name, section offset, material index) of every picture pane."""
    for m, off, sz in lay.sections:
        if m == b"pic1":
            name = d[off + 0x0C:off + 0x24].split(b"\0")[0].decode()
            yield name, off, struct.unpack_from("<H", d, off + 0x64)[0]


def fix_badge_layout(data: bytes) -> bytes:
    """Give every badge its own right piece (see BADGE_RIGHT)."""
    from .bflyt import Bflyt
    lay = Bflyt(data)
    if "icon_bind.bflim" not in _textures(data, lay):
        return data
    out = bytearray(data)
    pics = {name: off for name, off, _ in _pics(data, lay)}
    for name, off in pics.items():
        child = pics.get(name + "_1")
        if child is None:
            continue
        v0 = struct.unpack_from("<f", data, off + 0x68 + 4)[0]           # top-left v of the left piece
        badge = min(range(4), key=lambda i: abs(BADGE_ROWS[i] / 128 - v0))
        if abs(BADGE_ROWS[badge] / 128 - v0) > 0.02:
            continue
        x, y = BADGE_RIGHT[badge]
        u0, u1 = x / 64, (x + 32) / 64
        cv0, cv1 = struct.unpack_from("<f", data, child + 0x68 + 4)[0], struct.unpack_from("<f", data, child + 0x68 + 20)[0]
        if abs(cv0 - y / 128) > 0.02:                  # other row: take the v range from the table
            cv0, cv1 = y / 128, (y + BADGE_H) / 128
        struct.pack_into("<8f", out, child + 0x68, u0, cv0, u1, cv0, u0, cv1, u1, cv1)
        struct.pack_into("<f", out, child + 0x2C, SEAM_X[x])
    return bytes(out)


# ---------------------------------------------------------------------- LEVEL UP!

LEVELUP_IMAGES = {   # texture -> letter image (data/levelup/<name>.png)
    "timg/result_lv1.bflim": "L", "timg/result_lv2.bflim": "E", "timg/result_lv3.bflim": "V",
    "timg/result_lv4.bflim": "U", "timg/result_lv5.bflim": "P", "timg/result_lv6.bflim": "excl",
    "timg/result_lv7.bflim": "excl",
}
LEVELUP_PANES = ("L", "E", "V", "E", "L", "U", "P", "excl")     # panes result_lv1..8
LEVELUP_SPACE = 7                                               # gap between LEVEL and UP


def _letter(name: str) -> np.ndarray:
    from PIL import Image
    return np.array(Image.open(os.path.join(DATA, "levelup", name + ".png")).convert("RGBA"))


def levelup_texture(bflim: bytes, name: str) -> bytes:
    return texture.encode(_letter(LEVELUP_IMAGES[name]), bflim)


def fix_levelup_layout(data: bytes) -> bytes:
    from .bflyt import Bflyt
    lay = Bflyt(data)
    tex = _textures(data, lay)
    if "result_lv1.bflim" not in tex:
        return data
    by_letter = {}
    for t, letter in LEVELUP_IMAGES.items():
        by_letter.setdefault(letter, tex.index(t.split("/")[-1]))
    mats = _materials(data, lay)
    panes = {name: (off, mat) for name, off, mat in _pics(data, lay)}
    # letter widths (ink of the opaque part) -> centred x positions
    ink = []
    for letter in LEVELUP_PANES:
        a = _letter(letter)[..., 3] > 128
        cols = np.where(a.any(0))[0]
        ink.append((cols[0], cols[-1] + 1))
    xs, x = [], 0.0
    for i, (l, r) in enumerate(ink):
        if i == 5:
            x += LEVELUP_SPACE
        xs.append(x - l)                               # sprite left edge
        x += (r - l) + 1
    shift = -x / 2
    out = bytearray(data)
    for i, letter in enumerate(LEVELUP_PANES):
        p = panes.get(f"result_lv{i + 1}")
        if p is None:
            continue
        off, mat = p
        struct.pack_into("<H", out, mats[mat] + 0x28, by_letter[letter])          # texture map 0
        struct.pack_into("<f", out, off + 0x2C, xs[i] + shift + 16)               # pane centre x
    return bytes(out)


# ---------------------------------------------------------------------- pack patching

def patch_pack(pack_data: bytes, font_data: bytes) -> bytes | None:
    """The pack with its badge / level-up images and layouts in English, or None if it has none."""
    from .darc import Sarc
    from .pack import Pack
    pack = Pack.parse(pack_data)
    changed = False
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        repl = {}
        for n in sarc.nodes:
            raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
            if n.name == BADGE_TEXTURE:
                repl[n.name] = redraw_badges(raw, font_data)
            elif n.name in LEVELUP_IMAGES:
                repl[n.name] = levelup_texture(raw, n.name)
            elif n.name.endswith(".bflyt") and raw[:4] == b"FLYT":
                new = fix_levelup_layout(fix_badge_layout(raw))
                if new != raw:
                    repl[n.name] = new
        if repl:
            e.data = sarc.rebuild(repl)
            changed = True
    return pack.build() if changed else None
