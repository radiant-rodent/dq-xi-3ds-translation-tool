"""Latin name-entry keyboard (title screen, "Make Adventure Log").

The keyboard is a 12x6 grid drawn from a texture (title.pack: timg/win_keyboard_3d.bflim for
the hiragana page, win_keyboardB_3d.bflim for the katakana page); which character a key types
comes from a table in the executable:

  69 entries per page, 12 bytes each (u32 pointers to UTF-16 strings: character, voiced form,
  semi-voiced form), stored column by column (6 keys per column, the 12th column has 3).
  Column 11 holds the commands at rows 4-6 (page switch / delete / done); they are recognised
  by position, the strings are only names.

Every character string the table points to is used by nothing else, so the 4-byte constants
(one character + NUL) are rewritten in place and the voiced/semi-voiced pointers cleared.

Page 1 becomes A-Z a-z + punctuation, page 2 the accented letters that every font a name is
drawn with has (fontimport / fontcompose add them). Both textures are redrawn with the
message font's glyphs; the command keys get English labels.
"""
from __future__ import annotations

import struct

from .fontimport import _MORTON, Font

BASE = 0x100000                      # load address of .code
ENTRY = 12
PER_PAGE = 69
COMMANDS = (63, 64, 65)              # page switch, delete, done (column 11, rows 4-6)
TEXTURES = ("timg/win_keyboard_3d.bflim", "timg/win_keyboardB_3d.bflim")
TITLE_PACK = "/ui/title.pack"

# visual layout: 6 rows x 12 columns, "" = command key area
# The title's Save Code entry uses this keyboard too (page 1 only): page 1 holds every
# character of the Latin Save Code alphabet (codefix.SAVE_CODE_ALPHABET), the rest of the
# name characters (space, comma, apostrophe, ampersand) are on page 2.
PAGE_LATIN = [
    "ABCDEFGHIJ01",
    "KLMNOPQRST23",
    "UVWXYZ456789",
    "abcdefghij",
    "klmnopqrst",
    "uvwxyz-.!?",
]
ACCENT_UPPER = "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØŒÙÚÛÜÝŸ,'&"    # rows 0-2 (12 keys each)
ACCENT_LOWER = "àáâãäåæçèéêëìíîïñòóôõöøœùúûüýÿ"      # rows 3-5 (10 keys each)
# keys left over when a font lacks some letters: these instead of blank keys (a blank key is
# still selectable and types a space; user report 2026-10-16). No space key at all: the game
# accepts a name of nothing but spaces ("Happy to proceed with ' '?", tested 2026-10-16), and
# names of at most 6 letters hardly need one. (The Japanese pages have one full-width space.)
FILLERS = "-.():~/;+=#*@$_"
SPACE = " "
# fonts a name can be drawn with (menus, small 2D menus, messages)
NAME_FONTS = ("3D12", "2D9", "2D14SH", "3D14", "2D14")


def accent_page(supported) -> list[str]:
    """Second page: the accented letters every name font can draw, the rest filled with
    FILLERS (no blank keys, no space)."""
    fill = iter([c for c in FILLERS if c in supported] + [SPACE] * 66)
    up = [c for c in ACCENT_UPPER if c in supported][:36]
    lo = [c for c in ACCENT_LOWER if c in supported][:30]
    up += [next(fill) for _ in range(36 - len(up))]
    lo += [next(fill) for _ in range(30 - len(lo))]
    return ["".join(up[i:i + 12]) for i in range(0, 36, 12)] + ["".join(lo[i:i + 10]) for i in range(0, 30, 10)]
# Save Code entry (jumon mode): the next/previous code buttons (pics jumon_next/jumon_prev,
# 48x16) take their labels from black boxes below the key grid of the page-1 texture
JUMON_BOXES = ((0, 45, "Next"), (48, 93, "Back"))     # (left, right, label), rows JUMON_Y
JUMON_Y = (112, 128)
LABELS = {  # page -> (page switch, delete, done); もどる = "back"
    0: ("àéü", "Back", "Done"),
    1: ("ABC", "Back", "Done"),
}
# the cursor frame of each command key (measured in game 2026-10-16, screen x = texture x + 71):
# page switch x 221-248, delete / done 221-260 (the texture ends at 256). (centre, widest label);
# a wider label is squeezed. Centred at 239 for all three, "Delete" ran off the texture edge and
# the page-switch label sat 5 px right of its frame.
CMD_KEYS = ((234.5, 24), (239.5, 33), (239.5, 33))

# key cell origins in the upright 256x128 texture
COL_X = (0, 21, 42, 63, 84, 116, 137, 158, 179, 200, 222, 243)
ROW_Y = (0, 17, 34, 51, 68, 85)
KEY_W = 12
BASELINE = 13                        # below the cell top: room for accents on capitals


def _page_chars(rows: list[str]) -> list[str]:
    """Visual rows -> table order (column by column, commands as None)."""
    out = []
    for c in range(12):
        for r in range(6):
            if c == 11 and r >= 3:
                continue
            if c == 10 and r >= 3:
                out.append(None)
                continue
            line = rows[r]
            out.append(line[c] if c < len(line) else " ")
    assert len(out) == PER_PAGE
    return out


def find_table(code: bytes) -> int | None:
    """Offset of the first entry (あ) of the hiragana page."""
    first = None
    for o in range(0, len(code) - 4, 4):
        v = struct.unpack_from("<I", code, o)[0]
        if BASE <= v < BASE + len(code) - 4 and code[v - BASE:v - BASE + 4] == "あ\0".encode("utf-16-le"):
            nxt = struct.unpack_from("<I", code, o + ENTRY)[0]
            if BASE <= nxt < BASE + len(code) and code[nxt - BASE:nxt - BASE + 4] == "か\0".encode("utf-16-le"):
                if first is not None:
                    return None          # ambiguous
                first = o
    return first


def patch_code(code: bytes, accents: list[str] | None) -> bytes | None:
    """Latin keys in the name-entry table, or None if the table is not where it is expected."""
    t = find_table(code)
    if t is None:
        return None
    out = bytearray(code)
    pages = [PAGE_LATIN] + ([accents] if accents else [])
    # free one-character constants: every non-command string of the patched pages
    slots = []
    for p in range(len(pages)):
        for i in range(PER_PAGE):
            if i in COMMANDS:
                continue
            for k in range(3):
                v = struct.unpack_from("<I", code, t + (p * PER_PAGE + i) * ENTRY + k * 4)[0]
                if v and v not in slots and code[v - BASE + 2:v - BASE + 4] == b"\0\0":
                    slots.append(v)
    need = sum(1 for p in pages for c in _page_chars(p) if c is not None)
    if len(slots) < need:
        return None
    slots = iter(slots)
    for p, rows in enumerate(pages):
        for i, ch in enumerate(_page_chars(rows)):
            e = t + (p * PER_PAGE + i) * ENTRY
            if ch is None:
                continue
            v = next(slots)
            out[v - BASE:v - BASE + 4] = (ch + "\0").encode("utf-16-le")
            struct.pack_into("<III", out, e, v, 0, 0)
    return bytes(out)


# ---------------------------------------------------------------------- texture

_W, _H = 128, 256                    # stored size (the texture is stored rotated)


def _order():
    import numpy as np
    order = np.zeros((_H, _W), dtype=np.int64)
    tw = _W // 8
    for ty in range(_H // 8):
        for tx in range(tw):
            base = (ty * tw + tx) * 64
            for k, (dx, dy) in enumerate(_MORTON):
                order[ty * 8 + dy, tx * 8 + dx] = base + k
    return order


def _decode(buf: bytes):
    import numpy as np
    b = np.frombuffer(buf[:_W * _H], dtype=np.uint8)[_order()]
    return np.rot90(b >> 4, 1).copy(), np.rot90(b & 15, 1).copy()      # upright 128 x 256


def _encode(L, A) -> bytes:
    import numpy as np
    L, A = np.rot90(L, -1), np.rot90(A, -1)
    out = np.zeros(_W * _H, dtype=np.uint8)
    out[_order()] = (L.astype(np.uint8) << 4) | A.astype(np.uint8)
    return out.tobytes()


class _Pen:
    def __init__(self, font: Font):
        self.f = font
        a = font.cmap.get(ord("A"))
        rows = font.glyph(a)
        self.base = max(i for i, r in enumerate(rows) if any(r)) + 1     # bottom of 'A'

    def has(self, ch: str) -> bool:
        return ord(ch) in self.f.cmap or ch == " "

    def width(self, text: str) -> int:
        return sum(self.f.width(self.f.cmap[ord(c)])[2] if ord(c) in self.f.cmap else 3 for c in text)

    def draw(self, A, text: str, cx: float, baseline: int, max_width: int | None = None):
        import numpy as np
        w = self.width(text)
        if max_width and w > max_width:        # draw at full width, squeeze the columns
            T = np.zeros((A.shape[0], w + 2), dtype=A.dtype)
            self.draw(T, text, (w + 2) / 2, baseline)
            src = np.linspace(0, T.shape[1], max_width + 3)
            S = np.zeros((A.shape[0], max_width + 2), dtype=float)
            for i in range(max_width + 2):    # area average of the source columns
                a, b = src[i], src[i + 1]
                for j in range(int(a), min(int(np.ceil(b)), T.shape[1])):
                    S[:, i] += T[:, j] * (min(b, j + 1) - max(a, j))
                S[:, i] /= (b - a)
            x0 = round(cx - (max_width + 2) / 2)
            for i in range(S.shape[1]):
                if 0 <= x0 + i < A.shape[1]:
                    A[:, x0 + i] = np.maximum(A[:, x0 + i], np.minimum(15, np.round(S[:, i] * 1.15)).astype(A.dtype))
            return
        x = round(cx - w / 2)
        for c in text:
            gi = self.f.cmap.get(ord(c))
            if gi is None:
                x += 3
                continue
            left, gw, adv = self.f.width(gi)
            rows = self.f.glyph(gi)
            for r, row in enumerate(rows):
                y = baseline - self.base + r
                for cx_, v in enumerate(row[:gw]):
                    xx = x + left + cx_
                    if v and 0 <= y < A.shape[0] and 0 <= xx < A.shape[1]:
                        A[y, xx] = max(A[y, xx], v)
            x += adv


def _space_mark(A, cx: float, baseline: int):
    """The space key's label: an open box (U+2423 style) on the baseline."""
    x0, x1 = round(cx - 4), round(cx + 4)
    A[baseline - 1, x0:x1 + 1] = 15
    A[baseline - 4:baseline - 1, x0] = 15
    A[baseline - 4:baseline - 1, x1] = 15


def draw_page(tex: bytes, font: Font, rows: list[str], labels: tuple, boxes=()) -> bytes:
    import numpy as np
    L, A = _decode(tex)
    pen = _Pen(font)
    grid = ROW_Y[-1] + 17
    A[:grid, :] = 0                    # clear the key grid (keeps the boxes below it)
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch == SPACE:
                _space_mark(A, COL_X[c] + KEY_W / 2, ROW_Y[r] + BASELINE)
            else:
                pen.draw(A, ch, COL_X[c] + KEY_W / 2, ROW_Y[r] + BASELINE)
    for i, text in enumerate(labels):
        cx, mw = CMD_KEYS[i]
        pen.draw(A, text, cx, ROW_Y[3 + i] + BASELINE, max_width=mw)
    L[:grid][A[:grid] > 0] = 15
    y0, y1 = JUMON_Y
    for left, right, text in boxes:     # white text on the box (opaque, kept as it is)
        L[y0:y1, left:right + 1] = 0
        T = np.zeros_like(A)
        pen.draw(T, text, (left + right + 1) / 2, y0 + BASELINE)
        L[y0:y1, left:right + 1] = T[y0:y1, left:right + 1]
    return _encode(L, A) + tex[_W * _H:]


def patch_title_pack(pack_data: bytes, font_data: bytes, accents: list[str] | None) -> bytes:
    from .darc import Sarc
    from .pack import Pack
    font = Font(font_data)
    pack = Pack.parse(pack_data)
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        names = {n.name: n for n in sarc.nodes}
        if not all(t in names for t in TEXTURES):
            continue
        repl = {}
        pages = [(PAGE_LATIN, LABELS[0])] + ([(accents, LABELS[1])] if accents else [])
        for k, ((rows, labels), name) in enumerate(zip(pages, TEXTURES)):
            n = names[name]
            raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
            repl[name] = draw_page(raw, font, rows, labels, JUMON_BOXES if k == 0 else ())
        e.data = sarc.rebuild(repl)
        return pack.build()
    raise ValueError("name-entry keyboard textures not found in title.pack")


def supported(fonts: dict[str, bytes]) -> set[str]:
    """Characters every name font can draw."""
    sets = [set(chr(c) for c in Font(d).cmap) for n, d in fonts.items() if n in NAME_FONTS]
    return set.intersection(*sets) if sets else set()
