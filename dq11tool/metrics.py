"""Text measurement, automatic line wrapping and font sheet export for previews.

Widths are measured with the game's own fonts (after the English width fixes that the
exporter applies), so a translation can be checked against the space the original
Japanese line used.
"""
from __future__ import annotations

import hashlib
import io
import re
import struct
import threading
import zlib

from . import fontimport
from .bffnt import Bffnt
from .ctr.formats import open_image
from .fontpatch import patch_font

_TAG = re.compile(r"(\{\{|\}\}|\{[^{}]*\})")
VAR_WIDTH_CHARS = 8          # assumed length of a runtime variable (name, item...)


class FontSet:
    """Fonts loaded from the ROM, patched like the exporter does (imported Latin glyphs from
    `donor`, then the width fixes)."""

    _lock = threading.Lock()
    _cache: dict[tuple, "FontSet"] = {}

    def __init__(self, rom_path: str, donor: bytes | None = None, cache_dir: str | None = None):
        self.key = hashlib.sha1(donor).hexdigest()[:10] if donor else "orig"
        self.raw: dict[str, bytes] = {}
        self.fonts: dict[str, Bffnt] = {}
        f, ncsd, ncch = open_image(rom_path)
        try:
            r = ncch.romfs()
            for x in r.files:
                if x.path.startswith("/font/") and x.path.endswith(".bffnt"):
                    name = x.path.rsplit("/", 1)[-1][:-6]
                    data = patch_font(fontimport.apply(name, r.read_file(x), donor, cache_dir))
                    self.raw[name] = data
                    self.fonts[name] = Bffnt.parse(data)
        finally:
            f.close()

    @classmethod
    def get(cls, rom_path: str, donor: bytes | None = None, cache_dir: str | None = None) -> "FontSet":
        key = (rom_path, hashlib.sha1(donor).hexdigest() if donor else None)
        with cls._lock:
            fs = cls._cache.get(key)
            if fs is None:
                fs = cls._cache[key] = FontSet(rom_path, donor, cache_dir)
            return fs

    @classmethod
    def for_project(cls, project) -> "FontSet":
        """The fonts the exporter will write for this project."""
        import os
        return cls.get(project.rom_path, fontimport.active_donor(project),
                       os.path.join(project.workdir, "cache"))


def _visible_units(text: str):
    """Yield (kind, value) for a decoded string: ('ch', c), ('var', tag), ('nl', ''), ('page', '')."""
    for part in _TAG.split(text):
        if not part:
            continue
        if part == "{{":
            yield "ch", "{"
        elif part == "}}":
            yield "ch", "}"
        elif part.startswith("{") and part.endswith("}"):
            tag = part[1:-1]
            if tag.startswith("ruby:"):
                base = tag[5:].split("|", 1)[0]
                for c in base:
                    yield "ch", c
            elif tag.startswith("v:"):
                yield "var", part
            elif tag == "CR":
                yield "page", ""
            # other control codes are invisible
        else:
            for c in part:
                if c == "\n":
                    yield "nl", ""
                else:
                    yield "ch", c


def line_widths(text: str, font: Bffnt) -> list[int]:
    """Pixel width of every line. {CR} (wait for a button press) does not start a new line."""
    var_w = font.width_of("M") * VAR_WIDTH_CHARS * 2 // 3
    lines = [0]
    for kind, v in _visible_units(text):
        if kind == "ch":
            lines[-1] += font.width_of(v)
        elif kind == "var":
            lines[-1] += var_w
        elif kind == "nl":
            lines.append(0)
    return lines


def measure(text: str, font: Bffnt) -> dict:
    lines = line_widths(text, font)
    return {"max_width": max(lines) if lines else 0, "lines": len(lines),
            "lines_per_page": len(lines), "waits": text.count("{CR}")}


# The Japanese game fonts have no accented letters at all (the official PC names use é, ì, æ,
# ñ, ö ...) and no heart or arrow symbols; with an imported Latin font (fontimport) the accented
# letters exist. Characters a font lacks are replaced before they reach the game.
_FOLD_SPECIAL = {
    "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ø": "o", "Ø": "O", "ß": "ss", "ð": "d", "þ": "th",
    "¡": "", "¿": "", "♥": "♪", "♡": "♪", "❤": "♪", "↑": "^", "↓": "v", "←": "<", "→": ">",
    "«": '"', "»": '"', "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
}


def fold_to_font(text: str, font: Bffnt) -> str:
    """Replace characters the font cannot draw: accents are dropped (é -> e), ligatures spelt
    out (æ -> ae), symbols mapped to ones the font has; anything still missing is removed.
    Control/markup characters and everything the font has are left alone."""
    import unicodedata
    out = []
    for c in text:
        o = ord(c)
        if o < 0x20 or c in "{}" or o in font.cmap:
            out.append(c)
            continue
        rep = _FOLD_SPECIAL.get(c)
        if rep is None:
            rep = "".join(ch for ch in unicodedata.normalize("NFKD", c) if not unicodedata.combining(ch))
        out.append("".join(ch for ch in rep if ord(ch) in font.cmap or ord(ch) < 0x20))
    return "".join(out)


def missing_glyphs(text: str, font: Bffnt) -> set[str]:
    return {c for c in text if ord(c) >= 0x20 and c != "\n" and ord(c) not in font.cmap}


_AVG_SAMPLE ="The hero and his friends set out on a long journey to save the world, again."
_avg_cache: dict[int, float] = {}


def avg_char_width(font: Bffnt) -> float:
    """Average advance of ordinary English text (letters, spaces, punctuation)."""
    k = id(font)
    if k not in _avg_cache:
        _avg_cache[k] = sum(font.width_of(c) for c in _AVG_SAMPLE) / len(_AVG_SAMPLE)
    return _avg_cache[k]


def fit_limits(source: str, font: Bffnt) -> dict:
    """Space the original text occupies: max line width in px (at least 40, like the editor),
    number of lines, and a rough English character budget for the whole string."""
    src = line_widths(source, font)
    width = max(max(src) if src else 0, 40)
    lines = len(src)
    per_line = max(3, int(width / avg_char_width(font)))
    # wrapped text never fills every line completely
    budget = per_line if lines < 2 else int(per_line * lines * 0.85)
    return {"width": width, "lines": lines, "chars_per_line": per_line, "max_chars": budget}


def fits(source: str, translation: str, font: Bffnt) -> bool:
    """Same rule as the editor's warnings: not wider than the original, and a multi-line
    original box must not get more lines."""
    lim = fit_limits(source, font)
    tw = line_widths(translation, font)
    if max(tw) > lim["width"]:
        return False
    return lim["lines"] < 2 or len(tw) <= lim["lines"]


def wrap(translation: str, source: str, font: Bffnt, width: int | None = None,
         lines_per_page: int | None = None) -> str:
    """Re-flow an English translation to the line width of the source.

    Dialogue strings are one 3-line message box; the source marks each line end with
    {CR} (wait for a button press) when it wants the player to advance line by line.
    The same convention is applied to the wrapped English text.
    """
    src = measure(source, font)
    width = width or max(src["max_width"], font.width_of("M") * 8)
    src_lines = source.split("\n")
    waits_at_line_end = len(src_lines) > 1 and all(l.endswith("{CR}") for l in src_lines[:-1])
    plain_lines = [l.replace("{CR}", "") for l in src_lines]
    # Japanese dialogue indents continuation lines after 「; English dialogue does not
    indent = ""
    var_w = font.width_of("M") * VAR_WIDTH_CHARS * 2 // 3

    def w_of(s: str) -> int:
        tot = 0
        for kind, v in _visible_units(s):
            if kind == "ch":
                tot += font.width_of(v)
            elif kind == "var":
                tot += var_w
        return tot

    text = translation.replace("{CR}", " ").replace("\n", " ").replace("　", " ")
    words = [w for w in text.split(" ") if w]
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = (cur + " " + word) if cur else word
        limit = width - (w_of(indent) if lines else 0)
        if cur and w_of(cand) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    if lines:
        lines = [lines[0]] + [indent + l for l in lines[1:]]
    sep = "{CR}\n" if waits_at_line_end else "\n"
    return sep.join(lines)


# ---------------------------------------------------------------------- sheet export

def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    """Minimal grayscale+alpha PNG encoder."""
    raw = b"".join(b"\0" + r for r in rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 4, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _morton(i: int) -> tuple[int, int]:
    x = y = 0
    for b in range(3):
        x |= ((i >> (2 * b)) & 1) << b
        y |= ((i >> (2 * b + 1)) & 1) << b
    return x, y


_MORTON = [_morton(i) for i in range(64)]


def sheet_png(data: bytes, sheet_index: int = 0) -> bytes:
    """Decode one glyph sheet (A4 or A8/L8 tiled) to a white-on-alpha PNG."""
    font = Bffnt.parse(data)
    si = font.sheet_info
    w, h, fmt = si["sheet_w"], si["sheet_h"], si["format"]
    off = si["data_off"] + sheet_index * si["sheet_size"]
    buf = data[off:off + si["sheet_size"]]
    alpha = bytearray(w * h)
    bpp4 = fmt in (10, 11)  # L4 / A4
    tiles_x = w // 8
    p = 0
    for ty in range(h // 8):
        for tx in range(tiles_x):
            for i in range(64):
                if bpp4:
                    byte = buf[p >> 1]
                    v = (byte >> 4) if (p & 1) else (byte & 0xF)
                    v *= 17
                else:
                    v = buf[p]
                p += 1
                dx, dy = _MORTON[i]
                alpha[(ty * 8 + dy) * w + tx * 8 + dx] = v
    rows = []
    for y in range(h):
        row = bytearray(w * 2)
        row[0::2] = b"\xff" * w
        row[1::2] = alpha[y * w:(y + 1) * w]
        rows.append(bytes(row))
    return _png(w, h, rows)


def font_meta(data: bytes) -> dict:
    font = Bffnt.parse(data)
    si = font.sheet_info
    per_sheet = si["cols"] * si["rows"]
    glyphs = {}
    for cp, gi in font.cmap.items():
        wd = font.widths.get(gi, font.default_width)
        sheet, rem = divmod(gi, per_sheet)
        row, col = divmod(rem, si["cols"])
        glyphs[cp] = [sheet, col * (si["cell_w"] + 1) + 1, row * (si["cell_h"] + 1) + 1,
                      wd.left, wd.glyph_width, wd.char_width]
    return {"cell_w": si["cell_w"], "cell_h": si["cell_h"], "baseline": si["baseline"],
            "line_feed": font.line_feed, "height": font.height, "sheets": si["sheets"],
            "sheet_w": si["sheet_w"], "sheet_h": si["sheet_h"], "glyphs": glyphs}
