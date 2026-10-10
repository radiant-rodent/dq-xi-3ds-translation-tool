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
import json
import os
import struct
from dataclasses import dataclass, field

VERSION = 1                     # bump when the output of import_glyphs changes (cache key)
TARGET_FONTS = ("3D14", "2D14")  # message window fonts (3D mode / 2D mode)
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


def import_glyphs(target: bytes, donor: bytes, fullwidth: bool = True,
                  zero_kerning: bool = True) -> ImportResult:
    t = Font(target)
    d = Font(donor)
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

    def layout(shift: int):
        ch = max(t.ch, bottom + shift)
        for cw in range(max(t.cw, need_w), t.cw - 1, -1):
            cols, rows = t.sw // (cw + 1), t.sh // (ch + 1)
            if cols * rows * t.nsheets >= total:
                return cw, ch, cols, rows
        return None

    choice = None
    for allow_clip in (False, True):
        for shift in (ideal, ideal - 1, ideal + 1, ideal - 2, ideal + 2):
            if not allow_clip and top + shift < 0:
                continue
            lay = layout(shift)
            if lay:
                choice = (shift, lay)
                break
        if choice:
            break
    if not choice:
        raise ValueError("not enough room on the glyph sheet for the imported glyphs")
    shift, (cw, ch, cols, rows) = choice

    res = ImportResult(b"", cell=(cw, ch), shift=shift)
    # glyphs too wide for the chosen cell are left out (target keeps its own glyph)
    fits = {g for g, b in boxes.items() if b is None or b[2] <= cw}
    for c in list(wanted):
        if wanted[c] not in fits:
            res.skipped.append(chr(c))
            del wanted[c]
    new_codes = [c for c in new_codes if c in wanted]

    # glyph index -> pixel source
    gi_new = {c: t.glyph_count + i for i, c in enumerate(new_codes)}
    from_donor: dict[int, int] = {}                  # target glyph index -> donor glyph index
    for c, g in wanted.items():
        from_donor[gi_new.get(c, t.cmap.get(c))] = g
    count = t.glyph_count + len(new_codes)
    res.replaced = len(wanted) - len(new_codes)
    res.added = len(new_codes)

    # ---- new sheets
    old = t.sheets
    sheets = [bytearray(t.sw * t.sh) for _ in range(t.nsheets)]
    for gi in range(count):
        s, x, y = t.cell_origin(gi, cw, ch, cols, rows)
        dst = sheets[s]
        if gi in from_donor:
            px = donor_px[from_donor[gi]]
            for r in range(ch):
                sr = r - shift
                if 0 <= sr < len(px):
                    row = px[sr][:cw]
                    dst[(y + r) * t.sw + x:(y + r) * t.sw + x + len(row)] = row
        elif gi < t.glyph_count:
            os_, ox, oy = t.cell_origin(gi)
            src = old[os_]
            for r in range(t.ch):
                dst[(y + r) * t.sw + x:(y + r) * t.sw + x + t.cw] = \
                    src[(oy + r) * t.sw + ox:(oy + r) * t.sw + ox + t.cw]

    # ---- widths
    widths = {gi: t.width(gi) for gi in range(t.glyph_count)}
    for gi, g in from_donor.items():
        left, gw, cwid = d.width(g)
        widths[gi] = (left, min(gw, cw), cwid)
    max_char = max(w[2] for w in widths.values())

    # ---- character map: replaced codes keep their index; new codes go into the scan block
    blocks = [CmapBlock(b.start, b.end, b.method, b.offset, list(b.table), dict(b.scan))
              for b in t.cmap_blocks]
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
    for i, sh in enumerate(sheets):
        enc = _encode_sheet(sh, t.sw, t.sh)
        assert len(enc) == t.sheet_size
        out[t.sheet_off + i * t.sheet_size:t.sheet_off + (i + 1) * t.sheet_size] = enc
    out[t.finf + 10] = max(t.font_width, min(255, max_char))     # FINF max width

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
    replaced_codes = {c for c in wanted if c in t.cmap}
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
    res.data = bytes(out)
    return res


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


# ---------------------------------------------------------------------- donor management

def donor_path(workdir: str) -> str:
    return os.path.join(workdir, "fonts", "latin_donor.bcfnt")


def donor_info(workdir: str) -> dict | None:
    p = donor_path(workdir)
    if not os.path.isfile(p):
        return None
    info = {}
    try:
        with open(p + ".json", encoding="utf-8") as fp:
            info = json.load(fp)
    except (OSError, ValueError):
        pass
    info["path"] = p
    return info


def enabled(project) -> bool:
    """Project setting: use the imported Latin font (on by default once a donor is installed)."""
    return bool(project.config.get("latin_font", True))


def active_donor(project) -> bytes | None:
    """The donor font if one is installed and the project uses it, else None."""
    return load_donor(project.workdir) if enabled(project) else None


def load_donor(workdir: str | None) -> bytes | None:
    if not workdir:
        return None
    p = donor_path(workdir)
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as fp:
        return fp.read()


def _font_candidates(path: str):
    """(name, data) of every font in a .3ds/.cci game image, darc/SARC archive or font file."""
    with open(path, "rb") as fp:
        head = fp.read(4)
    if head in (b"CFNT", b"FFNT"):
        with open(path, "rb") as fp:
            yield os.path.basename(path), fp.read()
        return
    if head in (b"darc", b"SARC"):
        with open(path, "rb") as fp:
            yield from _fonts_in_archive(os.path.basename(path), fp.read())
        return
    from .ctr.formats import open_image
    f, ncsd, ncch = open_image(path)
    try:
        # some "decrypted" dumps keep the crypto flags set although the RomFS is plaintext,
        # so the flag alone is not trusted: try to read the RomFS either way
        try:
            romfs = ncch.romfs()
            files = romfs.files if romfs else None
        except Exception:
            files = None
        if not files:
            raise ValueError("cannot read the game's RomFS"
                             + ("" if ncch.is_decrypted else " (the image is encrypted; use a decrypted .3ds)"))
        for x in files:
            low = x.path.lower()
            if low.endswith((".bcfnt", ".bffnt")) or (low.endswith((".arc", ".sarc")) and "font" in low):
                data = romfs.read_file(x)
                if data[:4] in (b"CFNT", b"FFNT"):
                    yield x.path, data
                elif data[:4] in (b"darc", b"SARC"):
                    yield from _fonts_in_archive(x.path, data)
    finally:
        f.close()


def _fonts_in_archive(name: str, data: bytes):
    from .darc import Sarc, darc_files
    files = darc_files(data) if data[:4] == b"darc" else list(Sarc.parse(data).files.items())
    for n, d in files:
        if d[:4] in (b"CFNT", b"FFNT"):
            yield f"{name}/{n}", d


def _latin_coverage(data: bytes) -> int:
    try:
        f = Font(data)
    except Exception:
        return 0
    return sum(1 for c in f.cmap if 0x20 <= c < 0x180)


def install_donor(workdir: str, path: str) -> dict:
    """Find the narrow Latin font in a game image / archive / font file and store it in the
    workspace. Prefers Dragon Quest VII's message font; otherwise the Latin font closest to
    14 px with the most Latin-1 characters."""
    best = None
    for name, data in _font_candidates(path):
        try:
            f = Font(data)
        except Exception:
            continue
        cov = _latin_coverage(data)
        if cov < 90 or f.fmt not in (7, 8, 10, 11):
            continue
        # a western font: few glyphs, mostly Latin (Japanese fonts have thousands)
        western = len(f.cmap) < 1000
        score = (DONOR_NAME in name, western, -abs(f.height - 17), cov)
        if best is None or score > best[0]:
            best = (score, name, data, f)
    if best is None:
        raise ValueError("no suitable Latin font found in " + os.path.basename(path))
    _, name, data, f = best
    p = donor_path(workdir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fp:
        fp.write(data)
    info = {"source": os.path.abspath(path), "font": name, "height": f.height,
            "characters": len(f.cmap)}
    with open(p + ".json", "w", encoding="utf-8") as fp:
        json.dump(info, fp, indent=2)
    info["path"] = p
    return info


def remove_donor(workdir: str):
    p = donor_path(workdir)
    for x in (p, p + ".json"):
        if os.path.exists(x):
            os.remove(x)


def apply(name: str, data: bytes, donor: bytes | None, cache_dir: str | None = None) -> bytes:
    """Font `name` (e.g. '3D14') with the donor's Latin glyphs, if it is a message font."""
    if not donor or name not in TARGET_FONTS or data[:4] != b"FFNT":
        return data
    key = hashlib.sha1(data + donor + str(VERSION).encode()).hexdigest()[:16]
    cp = os.path.join(cache_dir, f"latin_{name}_{key}.bffnt") if cache_dir else None
    if cp and os.path.isfile(cp):
        with open(cp, "rb") as fp:
            return fp.read()
    out = import_glyphs(data, donor).data
    if cp:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cp, "wb") as fp:
            fp.write(out)
    return out
