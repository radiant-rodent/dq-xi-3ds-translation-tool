"""Point one layout's font list entry at another font file (same name length, so nothing moves).

The 2D item description pane uses the 2D14 font. Its condensed copy (fontcondense) is a separate
file, 2D1N, and the pane is re-pointed to it by renaming the entry in that layout's font list
(fnl1). Only that layout changes, so 2D14 stays as it is for every other pane and screen.
"""
from __future__ import annotations

import struct

from .bflyt import Bflyt


def rename_font(data: bytes, old: str, new: str) -> bytes:
    """Rename font `old` to `new` in the layout's fnl1 list; both names must have the same length."""
    if len(old) != len(new):
        raise ValueError("rename_font: the names must have the same length")
    for m, off, sz in Bflyt(data).sections:
        if m != b"fnl1":
            continue
        out = bytearray(data)
        n = struct.unpack_from("<H", data, off + 8)[0]
        hit = False
        for i in range(n):
            s = off + 12 + struct.unpack_from("<I", data, off + 12 + i * 4)[0]
            e = data.index(b"\0", s)
            if data[s:e] == old.encode("ascii"):
                out[s:e] = new.encode("ascii")
                hit = True
        if not hit:
            raise ValueError(f"rename_font: {old} is not in this layout's font list")
        return bytes(out)
    raise ValueError("rename_font: the layout has no font list")
