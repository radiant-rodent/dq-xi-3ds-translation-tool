"""Font width fixes for English text.

The Japanese fonts contain all ASCII glyphs, but some (2D mode fonts, credits font) use a
fixed advance of ~11-12px with the glyph ink centred, and the 3D14 font uses a full-width
space. That wastes a lot of horizontal space in text boxes designed for Japanese, so for
printable ASCII we rewrite the width table (CWDH): the glyph bitmaps are untouched, only
left bearing / advance change.
"""
from __future__ import annotations

import struct


def _cwdh_entries(data: bytes):
    """Yield (glyph_index, file_offset) for every width entry."""
    hsize = struct.unpack_from("<H", data, 6)[0]
    p = hsize
    ptr_width = struct.unpack_from("<I", data, p + 0x18)[0]
    while ptr_width:
        b = ptr_width - 8
        start, end, nxt = struct.unpack_from("<HHI", data, b + 8)
        for gi in range(start, end + 1):
            yield gi, b + 0x10 + (gi - start) * 3
        ptr_width = nxt


def _cmap(data: bytes) -> dict[int, int]:
    from .bffnt import Bffnt
    return Bffnt.parse(data).cmap


def patch_font(data: bytes) -> bytes:
    if data[:4] != b"FFNT":
        return data
    out = bytearray(data)
    hsize = struct.unpack_from("<H", data, 6)[0]
    height = data[hsize + 9]
    cmap = _cmap(data)
    width_off = dict(_cwdh_entries(data))

    def get(ch):
        gi = cmap.get(ord(ch))
        if gi is None or gi not in width_off:
            return None, None
        o = width_off[gi]
        left, gw, cw = struct.unpack_from("<bBB", out, o)
        return o, (left, gw, cw)

    letters = [get(c)[1] for c in "AaEemiIlW" if get(c)[1]]
    monospace = len({w[2] for w in letters}) == 1 and len(letters) >= 5
    if monospace:
        for code in range(0x21, 0x7F):
            o, w = get(chr(code))
            if not w:
                continue
            left, gw, cw = w
            if gw == 0:
                continue
            nl = 1
            ncw = min(255, nl + gw + 1)
            if ncw < cw:
                struct.pack_into("<bBB", out, o, nl, gw, ncw)
    # space: about a third of the line height
    o, w = get(" ")
    if w:
        target = max(3, round(height * 0.3))
        if w[2] > target + 1:
            struct.pack_into("<bBB", out, o, 0, 0, target)
    return bytes(out)
