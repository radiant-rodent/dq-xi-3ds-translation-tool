"""Condensed copy of a BFFNT font for the narrow text panes.

A pane that is too narrow for the English used to be squeezed at run time (font size x scaled
down): the 3DS samples the glyph texture at a fractional scale, so every glyph came out blurred
and stretched. Here the glyphs are condensed once, at build time, on the pixel grid instead:
each glyph loses the columns that carry the least ink (the gaps between letters first, then the
faintest edge columns), so the strokes keep their width and no pixel is ever interpolated. The
advance widths shrink by the same factor, so a condensed line measures the same as the squeezed
one did and the export's fit model stays valid.
"""
from __future__ import annotations

import struct

from .fontimport import Font, _encode_sheet


INK_KEEP = 0.8      # intensity of a condensed pixel: the max of its source columns, a little lighter


def _condense_rows(rows: list[bytes], gw: int, tw: int, cw: int) -> list[bytes]:
    """Each target column takes the strongest source column it covers (max over its share of the
    gw source columns), dimmed to INK_KEEP. Strokes keep their shape and stay on the pixel grid;
    nothing is interpolated between pixels."""
    out = []
    for r in rows:
        line = []
        for i in range(tw):
            a = int((i * gw) // tw)
            b = max(a + 1, -(-((i + 1) * gw) // tw))      # ceil((i+1)*gw/tw), at least one column
            line.append(min(15, round(max(r[a:min(b, gw)]) * INK_KEEP)))
        out.append(bytes(line) + bytes(cw - len(line)))
    return out


def condense(data: bytes, factor: float = 0.6) -> bytes:
    """A BFFNT (FFNT) font with every glyph condensed to `factor` of its width (glyph and advance)."""
    f = Font(data)
    if f.magic != b"FFNT" or f.fmt not in (10, 11):
        raise ValueError("condense: only A4/L4 BFFNT fonts are supported")
    sheets = [bytearray(s) for s in f.sheets]
    new = bytearray(data)
    widths: dict[int, tuple[int, int, int]] = {}
    for gi, (left, gw, adv) in f.widths.items():
        if gw == 0:
            continue
        tw = max(1, round(gw * factor))
        new_adv = max(tw + max(left, 0), round(adv * factor))
        widths[gi] = (left, tw, min(new_adv, 255))
        if tw == gw:
            continue
        sheet, x, y = f.cell_origin(gi)
        rows = _condense_rows(f.glyph(gi), gw, tw, f.cw)
        for r, line in enumerate(rows):
            base = (y + r) * f.sw + x
            sheets[sheet][base:base + f.cw] = line
    for i, sh in enumerate(sheets):
        enc = _encode_sheet(sh, f.sw, f.sh)
        if len(enc) != f.sheet_size:
            raise ValueError("condense: re-encoded sheet has the wrong size")
        off = f.sheet_off + i * f.sheet_size
        new[off:off + f.sheet_size] = enc
    # CWDH blocks: (left, glyph width, advance) per glyph, 3 bytes each, chained from the FINF
    # section's pw field
    q = struct.unpack_from("<I", data, f.hsize + 0x14 + 4)[0]
    while q:
        b = q - 8
        s, e, nx = struct.unpack_from("<HHI", data, b + 8)
        for gi in range(s, e + 1):
            if gi in widths:
                left, gw, adv = widths[gi]
                struct.pack_into("<bBB", new, b + 16 + (gi - s) * 3, left, gw, adv)
        q = nx
    return bytes(new)
