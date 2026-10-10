"""Read-only BFFNT (3DS "FFNT") font inspection: character map and glyph widths."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class GlyphWidth:
    left: int
    glyph_width: int
    char_width: int


@dataclass
class Bffnt:
    version: int
    line_feed: int
    height: int
    width: int
    ascent: int
    default_width: GlyphWidth
    cmap: dict[int, int] = field(default_factory=dict)       # codepoint -> glyph index
    widths: dict[int, GlyphWidth] = field(default_factory=dict)  # glyph index -> width
    sheet_info: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, data: bytes) -> "Bffnt":
        magic = data[:4]
        if magic not in (b"FFNT", b"CFNT"):
            raise ValueError("not a BFFNT")
        bom, hsize, version, fsize, nblocks = struct.unpack_from("<HHIIH", data, 4)
        p = hsize
        finf = data[p:p + 0x20]
        assert finf[:4] == b"FINF", finf[:4]
        # FINF: magic,size,fontType u8,height u8,width u8,ascent u8,lineFeed u16,alterCharIndex u16,
        #       defaultWidth(3 bytes: left, glyphWidth, charWidth), encoding u8, ptrGlyph, ptrWidth, ptrMap
        ftype, height, width, ascent, line_feed, alter = struct.unpack_from("<BBBBHH", data, p + 8)
        dl, dgw, dcw, enc = struct.unpack_from("<bBBB", data, p + 0x10)
        ptr_glyph, ptr_width, ptr_map = struct.unpack_from("<III", data, p + 0x14)
        f = cls(version, line_feed, height, width, ascent, GlyphWidth(dl, dgw, dcw))
        # TGLP
        tg = ptr_glyph - 8
        cw, ch, nsheets, maxw, sheet_size, baseline, fmt, cols, rows, sw, sh, sdata = \
            struct.unpack_from("<BBBBIHHHHHHI", data, tg + 8)
        f.sheet_info = dict(cell_w=cw, cell_h=ch, sheets=nsheets, max_w=maxw, sheet_size=sheet_size,
                            baseline=baseline, format=fmt, cols=cols, rows=rows, sheet_w=sw, sheet_h=sh,
                            data_off=sdata)
        # CWDH chain
        pw = ptr_width
        while pw:
            b = pw - 8
            start, end, nxt = struct.unpack_from("<HHI", data, b + 8)
            for gi in range(start, end + 1):
                l, gw, cwid = struct.unpack_from("<bBB", data, b + 0x10 + (gi - start) * 3)
                f.widths[gi] = GlyphWidth(l, gw, cwid)
            pw = nxt
        # CMAP chain
        pm = ptr_map
        while pm:
            b = pm - 8
            cstart, cend, method, _, nxt = struct.unpack_from("<HHHHI", data, b + 8)
            body = b + 0x14
            if method == 0:  # direct
                off = struct.unpack_from("<H", data, body)[0]
                for c in range(cstart, cend + 1):
                    f.cmap[c] = off + (c - cstart)
            elif method == 1:  # table
                for c in range(cstart, cend + 1):
                    gi = struct.unpack_from("<H", data, body + (c - cstart) * 2)[0]
                    if gi != 0xFFFF:
                        f.cmap[c] = gi
            elif method == 2:  # scan
                n = struct.unpack_from("<H", data, body)[0]
                for i in range(n):
                    c, gi = struct.unpack_from("<HH", data, body + 2 + i * 4)
                    f.cmap[c] = gi
            pm = nxt
        return f

    def width_of(self, ch: str) -> int:
        gi = self.cmap.get(ord(ch))
        if gi is None:
            return self.default_width.char_width
        return self.widths.get(gi, self.default_width).char_width
