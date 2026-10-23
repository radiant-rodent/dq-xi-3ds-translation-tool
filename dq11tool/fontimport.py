"""Narrow Latin glyphs for the game fonts, taken from another 3DS font.

Dragon Quest XI's fonts are Japanese fonts: their Latin letters are wide sans-serif glyphs
that waste a lot of the message window. The European release of Dragon Quest VII
(Fragments of the Forgotten Past) ships a western dialogue font ("m7_win_p14", a narrow
serif with accented letters) in the same 3DS font format. This module copies those glyphs
into DQXI's fonts:

  * glyphs of characters both fonts have (ASCII, punctuation ...) are replaced in place,
    keeping their glyph index;
  * characters only the donor has (é, ñ, ß, Œ ...) get new glyph indices in the free cells
    of the glyph sheet, a width entry and a character map entry;
  * full-width Latin letters (Ａ-Ｚ, used for names typed on the Latin keyboard) are drawn
    with the donor's ASCII glyphs as well;
  * the glyph sheet is re-laid-out with a taller/wider cell when the donor glyphs need it
    (the sheet keeps its size, so the font needs no extra memory) - every existing glyph
    keeps its pixels, top-left aligned in its cell;
  * kerning pairs of replaced characters are set to 0 (they were tuned for the old shapes).

Only fonts using a 4-bit sheet format (A4/L4, as all DQXI fonts do) can be patched.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field

VERSION = 12                    # bump when the output of import_glyphs changes (cache key)
# fonts of the top screen (message windows, menus); the bottom screen's 2D fonts keep their
# own pixel-style letters (fontcompose adds the accents there). NOT 2D14SH (3D battle captions
# and every field map place label): the DQ VII letters there, with or without a copied outline
# ring, looked much worse than the original (user, 2026-10-17) - keep its own glyphs
TARGET_FONTS = ("3D14", "3D12")
# the ring made the thin serif letters hard to read over the 3D scene (user, 2026-10-17): none
OUTLINE: dict[str, int] = {}
DONOR_NAME = "m7_win_p14"        # Dragon Quest VII (EU) message window font, 14 px


# ---------------------------------------------------------------------- texture swizzle

def _morton(i: int) -> tuple[int, int]:
    x = y = 0
    for b in range(3):
        x |= ((i >> (2 * b)) & 1) << b
        y |= ((i >> (2 * b + 1)) & 1) << b
    return x, y


_MORTON = [_morton(i) for i in range(64)]
_LO = bytes(i & 0xF for i in range(256))
_HI = bytes(i >> 4 for i in range(256))
_TO4 = bytes(i >> 4 for i in range(256))


def _decode_sheet(buf: bytes, w: int, h: int, fmt: int) -> bytearray:
    """Tiled 3DS texture -> linear w*h array of 4-bit values (0..15)."""
    if fmt in (10, 11):                       # L4 / A4: two texels per byte, low nibble first
        texels = bytearray(len(buf) * 2)
        texels[0::2] = buf.translate(_LO)
        texels[1::2] = buf.translate(_HI)
    elif fmt in (7, 8):                       # L8 / A8
        texels = bytearray(bytes(buf).translate(_TO4))
    else:
        raise ValueError(f"unsupported glyph sheet format {fmt}")
    tw = w // 8
    out = bytearray(w * h)
    for ty in range(h // 8):
        base = ty * tw * 64
        for k, (dx, dy) in enumerate(_MORTON):
            y = ty * 8 + dy
            out[y * w + dx:(y + 1) * w:8] = texels[base + k:base + tw * 64:64]
    return out


def _encode_sheet(lin: bytearray, w: int, h: int) -> bytes:
    """Linear 4-bit values -> tiled A4/L4 texture."""
    tw = w // 8
    texels = bytearray(w * h)
    for ty in range(h // 8):
        base = ty * tw * 64
        for k, (dx, dy) in enumerate(_MORTON):
            y = ty * 8 + dy
            texels[base + k:base + tw * 64:64] = lin[y * w + dx:(y + 1) * w:8]
    lo = texels[0::2]
    hi = texels[1::2]
    return bytes(a | (b << 4) for a, b in zip(lo, hi))


# ---------------------------------------------------------------------- font parsing

@dataclass
class CmapBlock:
    start: int
    end: int
    method: int                 # 0 direct, 1 table, 2 scan
    offset: int = 0             # direct: glyph index of `start`
    table: list = field(default_factory=list)   # table: glyph index per code (0xFFFF = none)
    scan: dict = field(default_factory=dict)    # scan: code -> glyph index


class Font:
    """BFFNT (FFNT, v4) or BCFNT (CFNT, v3) font with decoded glyph sheets."""

    def __init__(self, data: bytes):
        self.data = data
        self.magic = data[:4]
        if self.magic not in (b"FFNT", b"CFNT"):
            raise ValueError("not a 3DS font (FFNT/CFNT)")
        self.hsize = struct.unpack_from("<H", data, 6)[0]
        p = self.finf = self.hsize
        if self.magic == b"CFNT":
            self.line_feed = data[p + 9]
            dl, dgw, dcw = struct.unpack_from("<bBB", data, p + 12)
            pg, pw, pm = struct.unpack_from("<III", data, p + 16)
            self.height, self.font_width, self.ascent = struct.unpack_from("<BBB", data, p + 28)
            tg = pg - 8
            (self.cw, self.ch, self.baseline, self.maxw, self.sheet_size, self.nsheets, self.fmt,
             self.cols, self.rows, self.sw, self.sh, self.sheet_off) = \
                struct.unpack_from("<BBBBIHHHHHHI", data, tg + 8)
        else:
            self.height, self.font_width, self.ascent, self.line_feed = struct.unpack_from("<BBBH", data, p + 9)
            dl, dgw, dcw = struct.unpack_from("<bBB", data, p + 0x10)
            pg, pw, pm = struct.unpack_from("<III", data, p + 0x14)
            tg = pg - 8
            (self.cw, self.ch, self.nsheets, self.maxw, self.sheet_size, self.baseline, self.fmt,
             self.cols, self.rows, self.sw, self.sh, self.sheet_off) = \
                struct.unpack_from("<BBBBIHHHHHHI", data, tg + 8)
        self.tglp = tg
        self.tglp_size = struct.unpack_from("<I", data, tg + 4)[0]
        self.default_width = (dl, dgw, dcw)
        self.widths: dict[int, tuple[int, int, int]] = {}
        self.cwdh_blocks = []
        q = pw
        while q:
            b = q - 8
            s, e, nx = struct.unpack_from("<HHI", data, b + 8)
            self.cwdh_blocks.append((s, e))
            for gi in range(s, e + 1):
                self.widths[gi] = struct.unpack_from("<bBB", data, b + 16 + (gi - s) * 3)
            q = nx
        self.cmap_blocks: list[CmapBlock] = []
        self.cmap: dict[int, int] = {}
        q = pm
        while q:
            b = q - 8
            cs, ce, meth, _, nx = struct.unpack_from("<HHHHI", data, b + 8)
            body = b + 0x14
            blk = CmapBlock(cs, ce, meth)
            if meth == 0:
                blk.offset = struct.unpack_from("<H", data, body)[0]
                for c in range(cs, ce + 1):
                    self.cmap[c] = blk.offset + c - cs
            elif meth == 1:
                blk.table = list(struct.unpack_from(f"<{ce - cs + 1}H", data, body))
                for c, g in zip(range(cs, ce + 1), blk.table):
                    if g != 0xFFFF:
                        self.cmap[c] = g
            elif meth == 2:
                n = struct.unpack_from("<H", data, body)[0]
                for i in range(n):
                    c, g = struct.unpack_from("<HH", data, body + 2 + i * 4)
                    blk.scan[c] = g
                    self.cmap.setdefault(c, g)
            else:
                raise ValueError(f"unknown character map method {meth}")
            self.cmap_blocks.append(blk)
            q = nx
        # every top-level block, for blocks this module does not interpret (KRNG ...)
        self.blocks = []
        p = self.hsize
        while p + 8 <= len(data):
            mg = data[p:p + 4]
            sz = struct.unpack_from("<I", data, p + 4)[0]
            if sz < 8:
                break
            self.blocks.append((mg, p, sz))
            p += sz
        self.glyph_count = max(list(self.widths) + list(self.cmap.values()), default=-1) + 1
        self._sheets: list[bytearray] | None = None

    # -- glyph pixels
    @property
    def sheets(self) -> list[bytearray]:
        if self._sheets is None:
            self._sheets = [
                _decode_sheet(self.data[self.sheet_off + i * self.sheet_size:
                                        self.sheet_off + (i + 1) * self.sheet_size],
                              self.sw, self.sh, self.fmt)
                for i in range(self.nsheets)]
        return self._sheets

    def cell_origin(self, gi: int, cw=None, ch=None, cols=None, rows=None) -> tuple[int, int, int]:
        cw, ch = cw or self.cw, ch or self.ch
        cols, rows = cols or self.cols, rows or self.rows
        sheet, rem = divmod(gi, cols * rows)
        r, c = divmod(rem, cols)
        return sheet, c * (cw + 1) + 1, r * (ch + 1) + 1

    def glyph(self, gi: int) -> list[bytes]:
        """Rows (cw bytes each, values 0..15) of the glyph cell."""
        s, x, y = self.cell_origin(gi)
        sh = self.sheets[s]
        return [bytes(sh[(y + r) * self.sw + x:(y + r) * self.sw + x + self.cw]) for r in range(self.ch)]

    def width(self, gi: int) -> tuple[int, int, int]:
        return self.widths.get(gi, self.default_width)


def _ink_box(rows: list[bytes]) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) exclusive bounds of the non-zero pixels."""
    ys = [i for i, r in enumerate(rows) if any(r)]
    if not ys:
        return None
    xs = [i for i in range(len(rows[0])) if any(r[i] for r in rows)]
    return xs[0], ys[0], xs[-1] + 1, ys[-1] + 1


# ---------------------------------------------------------------------- import

@dataclass
class ImportResult:
    data: bytes
    replaced: int = 0
    added: int = 0
    skipped: list = field(default_factory=list)   # characters whose donor glyph did not fit
    cell: tuple = (0, 0)
    shift: int = 0


def _outlined(rows: list[bytes], o: int) -> list[bytes]:
    """rows padded by o pixels on every side, with an outline ring around the ink like the
    one baked into 2D14SH: 8 next to the ink, fading outwards (5, 2)."""
    h, w = len(rows), len(rows[0]) if rows else 0
    W, H = w + 2 * o, h + 2 * o
    core = [[0] * W for _ in range(H)]
    for y, r in enumerate(rows):
        for x, v in enumerate(r):
            core[y + o][x + o] = v
    ink = [(x, y) for y in range(H) for x in range(W) if core[y][x] >= 6]
    ring = [[0] * W for _ in range(H)]
    for x, y in ink:
        for dy in range(-o, o + 1):
            for dx in range(-o, o + 1):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H:
                    d2 = dx * dx + dy * dy
                    v = 8 if d2 <= 2 else 5 if d2 <= 4 else 2
                    if v > ring[yy][xx]:
                        ring[yy][xx] = v
    return [bytes(max(core[y][x], ring[y][x]) for x in range(W)) for y in range(H)]


def import_glyphs(target: bytes, donor: bytes, fullwidth: bool = True,
                  zero_kerning: bool = True, spacing: int = 1, outline: int = 0) -> ImportResult:
    """spacing: pixels added to every inked glyph's advance. DQ VII's glyphs have no side
    bearings at all (its layouts add character spacing), so without it punctuation runs into
    the neighbouring letters ("j'" reads as one glyph). outline: a ring of this many pixels
    around every donor glyph (fonts with a baked outline, OUTLINE); the advance stays."""
    t = Font(target)
    d = Font(donor)
    if outline:
        d.glyph = (lambda g, _orig=d.glyph: _outlined(_orig(g), outline))
        d.width = (lambda g, _orig=d.width: (lambda l, gw, cw: (l - outline, gw + 2 * outline, cw))(*_orig(g)))
    if t.magic != b"FFNT":
        raise ValueError("only BFFNT (FFNT) target fonts are supported")
    if t.fmt not in (10, 11):
        raise ValueError("target glyph sheet is not 4-bit (A4/L4)")

    # characters to take from the donor: everything it has; full-width forms use the ASCII glyph
    wanted: dict[int, int] = {}                     # target code -> donor glyph index
    for c, g in d.cmap.items():
        if c >= 0x20:
            wanted[c] = g
    if fullwidth:
        for c in range(0xFF01, 0xFF5F):
            if c in t.cmap and (c - 0xFEE0) in d.cmap:
                wanted[c] = d.cmap[c - 0xFEE0]
    donor_px = {g: d.glyph(g) for g in set(wanted.values())}
    boxes = {g: _ink_box(px) for g, px in donor_px.items()}

    # vertical placement: put the donor's baseline on the target's (bottom of 'A')
    ta = _ink_box(t.glyph(t.cmap[0x41])) if 0x41 in t.cmap else None
    da = boxes.get(d.cmap.get(0x41))
    ideal = (ta[3] - da[3]) if (ta and da) else (t.baseline - d.baseline)
    inked = [b for b in boxes.values() if b]
    top = min(b[1] for b in inked)
    bottom = max(b[3] for b in inked)
    need_w = max(max(b[2] for b in inked), max(d.width(g)[1] for g in donor_px))

    new_codes = sorted(c for c in wanted if c not in t.cmap)
    total = t.glyph_count + len(new_codes)

    def layout(shift: int, lift: int):
        ch = max(t.ch + lift, bottom + shift + lift)
        for cw in range(max(t.cw, need_w), t.cw - 1, -1):
            cols, rows = t.sw // (cw + 1), t.sh // (ch + 1)
            if cols * rows * t.nsheets >= total:
                return cw, ch, cols, rows
        return None

    # glyphs that would stick out above the cell either lift the whole sheet (existing glyphs
    # and the baseline move down) or are placed lower / clipped when the sheet has no room
    choice = None
    for mode in ("lift", "fit", "clip"):
        for shift in (ideal, ideal - 1, ideal + 1, ideal - 2, ideal + 2):
            lift = max(0, -(top + shift)) if mode == "lift" else 0
            if mode == "fit" and top + shift < 0:
                continue
            lay = layout(shift, lift)
            if lay:
                choice = (shift, lift, lay)
                break
        if choice:
            break
    if not choice:
        raise ValueError("not enough room on the glyph sheet for the imported glyphs")
    shift, lift, (cw, ch, cols, rows) = choice

    res = ImportResult(b"", cell=(cw, ch), shift=shift)
    # glyphs too wide for the chosen cell are left out (target keeps its own glyph)
    fits = {g for g, b in boxes.items() if b is None or b[2] <= cw}
    for c in list(wanted):
        if wanted[c] not in fits:
            res.skipped.append(chr(c))
            del wanted[c]

    pad = d.width(d.cmap[0x20])[2] if 0x20 in d.cmap else 4
    glyphs = {}
    for c, g in wanted.items():
        px = donor_px[g]
        cell = [px[r - shift - lift][:cw] if 0 <= r - shift - lift < len(px) else b"" for r in range(ch)]
        left, gw, cwid = d.width(g)
        if boxes.get(g):
            cwid += spacing
        if 0xFF21 <= c <= 0xFF3A or 0xFF41 <= c <= 0xFF5A:
            # full-width letters are what the game appends to monster names ("スライムＡ"):
            # a space in front keeps "Slime A" apart, as the wide Japanese glyph did
            left += pad
            cwid += pad
        glyphs[c] = (cell, (left, min(gw, cw), cwid))
    res.data, res.replaced, res.added = _rewrite(target, t, glyphs, (cw, ch, cols, rows), lift, zero_kerning)
    return res


def _rewrite(target: bytes, t: Font, glyphs: dict, cell: tuple, lift: int,
             zero_kerning: bool = True, reuse: dict | None = None,
             drop: set | frozenset = frozenset()) -> tuple[bytes, int, int]:
    """Font `target` with new/replaced glyphs.

    glyphs: code -> (rows, (left, glyph width, advance)); rows are pixel rows (0..15) in the
            new cell, row 0 = top of the new cell.
    cell:   new (cell w, cell h, columns, rows) of the glyph sheet.
    lift:   existing glyphs move down by this many rows (and the baseline with them), which
            makes room above them, e.g. for accents on capitals.
    reuse:  new code -> index of an existing glyph whose cell it takes over (its old character
            must be in `drop`); other new codes get cells after the last glyph.
    drop:   codes removed from the character map (scan and table blocks only).
    Returns (data, replaced, added)."""
    cw, ch, cols, rows = cell
    reuse = reuse or {}
    new_codes = sorted(c for c in glyphs if c not in t.cmap)
    appended = [c for c in new_codes if c not in reuse]
    gi_new = {c: t.glyph_count + i for i, c in enumerate(appended)}
    gi_new.update({c: reuse[c] for c in new_codes if c in reuse})
    by_gi = {gi_new.get(c, t.cmap.get(c)): v for c, v in glyphs.items()}
    count = t.glyph_count + len(appended)
    if count > cols * rows * t.nsheets:
        raise ValueError("not enough room on the glyph sheet")

    # ---- new sheets
    old = t.sheets
    sheets = [bytearray(t.sw * t.sh) for _ in range(t.nsheets)]
    for gi in range(count):
        s, x, y = t.cell_origin(gi, cw, ch, cols, rows)
        dst = sheets[s]
        if gi in by_gi:
            for r, row in enumerate(by_gi[gi][0][:ch]):
                row = row[:cw]
                dst[(y + r) * t.sw + x:(y + r) * t.sw + x + len(row)] = row
        elif gi < t.glyph_count:
            os_, ox, oy = t.cell_origin(gi)
            src = old[os_]
            for r in range(min(t.ch, ch - lift)):
                dst[(y + lift + r) * t.sw + x:(y + lift + r) * t.sw + x + t.cw] =                     src[(oy + r) * t.sw + ox:(oy + r) * t.sw + ox + t.cw]

    # ---- widths
    widths = {gi: t.width(gi) for gi in range(t.glyph_count)}
    for gi, (_, w) in by_gi.items():
        widths[gi] = w
    max_char = max(w[2] for w in widths.values())

    # ---- character map: replaced codes keep their index; new codes go into the scan block
    blocks = [CmapBlock(b.start, b.end, b.method, b.offset, list(b.table), dict(b.scan))
              for b in t.cmap_blocks]
    for b in blocks:
        for c in drop:
            b.scan.pop(c, None)
            if b.method == 1 and b.start <= c <= b.end:
                b.table[c - b.start] = 0xFFFF
    scan_block = next((b for b in blocks if b.method == 2), None)
    for c in new_codes:
        for b in blocks:
            if b.method == 1 and b.start <= c <= b.end:
                b.table[c - b.start] = gi_new[c]
                break
        else:
            if scan_block is None:
                scan_block = CmapBlock(c, c, 2)
                blocks.append(scan_block)
            scan_block.scan[c] = gi_new[c]
            scan_block.start = min(scan_block.start, c)
            scan_block.end = max(scan_block.end, c)

    # ---- write the file
    out = bytearray(target[:t.tglp + t.tglp_size])
    struct.pack_into("<BB", out, t.tglp + 8, cw, ch)
    out[t.tglp + 11] = max(t.maxw, min(255, max_char))
    struct.pack_into("<HH", out, t.tglp + 20, cols, rows)
    if lift:
        struct.pack_into("<H", out, t.tglp + 16, t.baseline + lift)       # FFNT: baseline in TGLP
    for i, sh in enumerate(sheets):
        enc = _encode_sheet(sh, t.sw, t.sh)
        assert len(enc) == t.sheet_size
        out[t.sheet_off + i * t.sheet_size:t.sheet_off + (i + 1) * t.sheet_size] = enc
    # FINF width stays the original one: text boxes draw glyphs at fontSize.x / FINF width,
    # and the layouts' font sizes equal the original widths. A larger value (the widest
    # imported glyph) squeezed all text horizontally (3D12 to 13/19), so the game no longer
    # matched the editor's measurements.

    def align4():
        while len(out) % 4:
            out.append(0)

    # width table: one block for all glyphs
    align4()
    cwdh_at = len(out)
    body = b"".join(struct.pack("<bBB", *widths[gi]) for gi in range(count))
    blk = struct.pack("<HHI", 0, count - 1, 0) + body
    blk += b"\0" * (-(8 + len(blk)) % 4)
    out += b"CWDH" + struct.pack("<I", 8 + len(blk)) + blk

    # character maps, chained in the original order (lookups stop at the first block whose
    # range contains the character, so the order matters)
    cmap_at = []
    for b in blocks:
        align4()
        cmap_at.append(len(out))
        if b.method == 0:
            body = struct.pack("<HH", b.offset, 0)
        elif b.method == 1:
            body = struct.pack(f"<{len(b.table)}H", *b.table)
        else:
            items = sorted(b.scan.items())
            body = struct.pack("<H", len(items)) + b"".join(struct.pack("<HH", c, g) for c, g in items)
        blk = struct.pack("<HHHHI", b.start, b.end, b.method, 0, 0) + body
        blk += b"\0" * (-(8 + len(blk)) % 4)
        out += b"CMAP" + struct.pack("<I", 8 + len(blk)) + blk
    for i, at in enumerate(cmap_at[:-1]):
        struct.pack_into("<I", out, at + 0x10, cmap_at[i + 1] + 8)

    # other blocks (kerning ...) are copied
    nblocks = 2 + 1 + len(blocks)
    replaced_codes = {c for c in glyphs if c in t.cmap}
    for mg, p, sz in t.blocks:
        if mg in (b"FINF", b"TGLP", b"CWDH", b"CMAP"):
            continue
        raw = bytearray(target[p:p + sz])
        if mg == b"KRNG" and zero_kerning:
            _zero_kerning(raw, replaced_codes)
        align4()
        out += raw
        nblocks += 1

    if t.magic == b"FFNT":
        struct.pack_into("<II", out, t.finf + 0x18, cwdh_at + 8, cmap_at[0] + 8)
    struct.pack_into("<I", out, 0x0C, len(out))
    struct.pack_into("<H", out, 0x10, nblocks)
    return bytes(out), len(glyphs) - len(new_codes), len(new_codes)


def _zero_kerning(raw: bytearray, codes: set[int]):
    """KRNG (3DS v4): u16 first-char count, {u16 char, u16 offset/2 from +8} entries,
    each pointing at u16 count, {u16 second char, s16 adjustment} pairs."""
    try:
        n = struct.unpack_from("<H", raw, 8)[0]
        for i in range(n):
            first, off = struct.unpack_from("<HH", raw, 10 + i * 4)
            o = 8 + off * 2
            cnt = struct.unpack_from("<H", raw, o)[0]
            for j in range(cnt):
                second = struct.unpack_from("<H", raw, o + 2 + j * 4)[0]
                if first in codes or second in codes:
                    struct.pack_into("<h", raw, o + 4 + j * 4, 0)
    except struct.error:
        pass   # unknown layout: leave it alone


# ---------------------------------------------------------------------- bundled donor

DONOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", DONOR_NAME + ".bcfnt")
_donor: bytes | None = None


DONOR_ARC = "/LAYOUT/system_font14__.arc"   # in Dragon Quest VII (EU) RomFS, member font/<DONOR_NAME>.bcfnt
DONOR_SHA256 = "40a0e4d3886b46f5793f7ce5623cc21ae6ab3401185de8452944269f8489edd3"


def donor() -> bytes:
    """The Latin font from Dragon Quest VII (EU) (not distributed with the tool: extract it from
    your own dump with `python -m dq11tool.fontimport DQVII.3ds`)."""
    global _donor
    if _donor is None:
        if not os.path.isfile(DONOR_FILE):
            raise FileNotFoundError(
                f"{DONOR_FILE} is missing: extract it from your Dragon Quest VII (Europe) dump with "
                "`python -m dq11tool.fontimport <DQVII.3ds>`")
        with open(DONOR_FILE, "rb") as fp:
            _donor = fp.read()
    return _donor


def extract_donor(rom_path: str) -> str:
    """Copy the donor font out of a decrypted Dragon Quest VII (EU) .3ds into DONOR_FILE."""
    from .ctr.formats import open_image
    from .darc import darc_files
    f, _ncsd, ncch = open_image(rom_path)
    try:
        romfs = ncch.romfs()
        arc = next((rf for rf in romfs.files if rf.path == DONOR_ARC), None)
        if arc is None:
            raise ValueError(f"{rom_path} has no {DONOR_ARC} (not Dragon Quest VII (EU)?)")
        data = dict(darc_files(romfs.read_file(arc))).get(f"font/{DONOR_NAME}.bcfnt")
    finally:
        f.close()
    if data is None or hashlib.sha256(data).hexdigest() != DONOR_SHA256:
        raise ValueError("unexpected font data (different release of Dragon Quest VII?)")
    os.makedirs(os.path.dirname(DONOR_FILE), exist_ok=True)
    with open(DONOR_FILE, "wb") as fp:
        fp.write(data)
    return DONOR_FILE


BLANK_CODES = tuple(range(0xE000, 0xE00B))   # textcodec grammar markers, {noun}, {grp}: drawn as nothing
# Japanese-only glyphs the game appends at runtime, drawn as nothing: U+334D is the small "こ"
# item counter ("2㍍" in the Item Bag and smithing lists; the executable builds it, no string
# to translate - user 2026-10-17)
BLANKED_CODES = (0x334D,)


def add_blank(data: bytes, codes=BLANK_CODES + BLANKED_CODES) -> bytes:
    """Empty glyphs with zero advance for `codes` (kept on the current sheet layout); codes the
    font already has (BLANKED_CODES) are emptied."""
    t = Font(data)
    todo = [c for c in codes if c not in t.cmap or c in BLANKED_CODES]
    if not todo or t.magic != b"FFNT":
        return data
    glyphs = {c: ([b""] * t.ch, (0, 0, 0)) for c in todo}
    return _rewrite(data, t, glyphs, (t.cw, t.ch, t.cols, t.rows), 0, zero_kerning=False)[0]


def build(name: str, data: bytes, font_fixes: bool = True, cache_dir: str | None = None) -> bytes:
    """A game font as the exporter writes it: the bundled Latin glyphs (message fonts only),
    the English width fixes, then accented letters composed from the font's own glyphs where
    they are still missing. Cached, the composing takes a few seconds per font."""
    from . import fontcompose
    from .fontpatch import patch_font
    if data[:4] != b"FFNT":
        return data
    key = hashlib.sha1(data + f"{VERSION}/{fontcompose.VERSION}/{font_fixes}".encode()
                       ).hexdigest()[:16]
    cp = os.path.join(cache_dir, f"font_{name}_{key}.bffnt") if cache_dir else None
    if cp and os.path.isfile(cp):
        with open(cp, "rb") as fp:
            return fp.read()
    out = data
    if name in TARGET_FONTS:
        out = import_glyphs(out, donor(), outline=OUTLINE.get(name, 0)).data
    if font_fixes:
        out = patch_font(out)
    out = fontcompose.compose(out)
    out = add_blank(out)
    if cp:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cp, "wb") as fp:
            fp.write(out)
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: python -m dq11tool.fontimport <Dragon Quest VII (Europe) decrypted .3ds>")
    print("wrote", extract_donor(sys.argv[1]))
