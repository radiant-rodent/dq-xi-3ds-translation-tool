"""BXON: DQXI's binary serialized parameter/text tables.

Layout:
  0x00 "BXON"  0x04 u32 version (1)  0x08 u32 type hash  0x0C type name (NUL padded to 0x20)
  0x20 root: u32 count, u32 self-relative pointer to the element array
  Every allocation (array data or string) is 4-byte aligned and preceded by the
  marker "&&&&" (0x26262626). Strings are UTF-16LE, NUL terminated and padded with
  '@' (0x40) bytes to 4-byte alignment. Fields referencing data are self-relative
  u32 offsets (target = field_position + value); arrays are (u32 count, u32 relptr).

The schema is not stored in the file, so strings are located structurally: a block
whose payload is a well-formed UTF-16 string is a string block, and any 4-aligned
u32 in a non-string block that points exactly at a string block is a reference to it.

Edited strings are written in place when they fit the original block, otherwise
appended as new blocks at the end of the file with their references re-pointed.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from .textcodec import decode_utf16, encode_utf16

MARK = b"&&&&"


@dataclass
class BxonString:
    offset: int                 # payload start (pointer target)
    capacity: int               # payload bytes available (incl. terminator and padding)
    raw: bytes                  # UTF-16LE payload without terminator
    refs: list[int] = field(default_factory=list)   # positions of u32 fields pointing here

    @property
    def text(self) -> str:
        return decode_utf16(self.raw)


@dataclass
class Bxon:
    data: bytes
    type_name: str
    strings: list[BxonString]

    @classmethod
    def parse(cls, data: bytes) -> "Bxon":
        if data[:4] != b"BXON":
            raise ValueError("not BXON")
        type_name = data[0x0C:0x20].split(b"\0", 1)[0].decode("ascii", "replace")
        n = len(data)
        # block starts: 4-aligned positions right after a marker
        starts = []
        pos = data.find(MARK, 0x20)
        while pos != -1:
            if pos % 4 == 0:
                starts.append(pos + 4)
                pos = data.find(MARK, pos + 4)
            else:
                pos = data.find(MARK, pos + 1)
        bounds = starts[1:] + [n + 4]
        strings: dict[int, BxonString] = {}
        string_ranges = []
        for s, nxt in zip(starts, bounds):
            end = nxt - 4  # next marker position (or EOF)
            payload = data[s:end]
            st = _as_string(payload, at_eof=(end == n))
            if st is not None:
                strings[s] = BxonString(s, len(payload), st)
                string_ranges.append((s, end))
        words = struct.unpack_from(f"<{n // 4}I", data, 0)
        block_size = {s: (nxt - 4) - s for s, nxt in zip(starts, bounds)}
        block_starts = set(starts)

        def pointer_array(s: int) -> bool:
            # every word is a relative pointer to another block: an array of pointers, not text
            size = block_size[s]
            if size == 0 or size % 4:
                return False
            return all(words[(s >> 2) + i] and ((s + 4 * i + words[(s >> 2) + i]) & 0xFFFFFFFF) in block_starts
                       for i in range(size // 4))

        stringish = {s for s, nxt in zip(starts, bounds) if s in strings or _is_string_block(data[s:nxt - 4])}
        # short blocks such as "08 00 00 00" are both a valid one-char string and a valid
        # pointer array; the way the walker reaches them decides what they are
        ambiguous = {s for s in stringish if pointer_array(s)}
        refs, arrays = _structural_refs(n, words, block_size, strings, stringish, ambiguous)
        for base in arrays:
            strings.pop(base, None)
        for t, positions in refs.items():
            if t in strings:
                strings[t].refs.extend(sorted(set(positions)))
        return cls(data, type_name, sorted(strings.values(), key=lambda b: b.offset))

    @staticmethod
    def heuristic_refs(data: bytes, strings: list["BxonString"]) -> dict[int, list[int]]:
        """Old approach (every u32 that points at a string). Kept for diagnostics only:
        it produces false positives on flag fields such as 0x0000FF00."""
        n = len(data)
        words = struct.unpack_from(f"<{n // 4}I", data, 0)
        by_off = {s.offset: s for s in strings}
        ranges = sorted((s.offset, s.offset + s.capacity) for s in strings)
        out: dict[int, list[int]] = {}
        ri = 0
        for i in range(8, n // 4):
            p = i * 4
            while ri < len(ranges) and ranges[ri][1] <= p:
                ri += 1
            if ri < len(ranges) and ranges[ri][0] <= p < ranges[ri][1]:
                continue
            v = words[i]
            if v and not v & 3:
                t = (p + v) & 0xFFFFFFFF
                if t in by_off:
                    out.setdefault(t, []).append(p)
        return out

    def rebuild(self, replacements: dict[int, str]) -> bytes:
        """replacements: string offset -> new text. Returns new file bytes.

        Offsets that cannot be applied safely (unknown, or too long for their slot while no
        reference to them is known) are listed in self.skipped afterwards."""
        out = bytearray(self.data)
        by_off = {s.offset: s for s in self.strings}
        self.skipped = []
        for off, text in replacements.items():
            s = by_off.get(off)
            if s is None:
                self.skipped.append((off, "not a string in this file"))
                continue
            raw = encode_utf16(text)
            payload = raw + b"\0\0"
            payload += b"@" * (-len(payload) % 4)
            if len(payload) > s.capacity and not s.refs:
                self.skipped.append((off, "too long for its slot and no pointer to it is known"))
                continue
            if len(payload) <= s.capacity:
                payload += b"@" * (s.capacity - len(payload))
                out[off:off + s.capacity] = payload
                continue
            # append as new block
            while len(out) % 4:
                out += b"@"
            out += MARK
            new_off = len(out)
            out += payload
            for r in s.refs:
                struct.pack_into("<I", out, r, (new_off - r) & 0xFFFFFFFF)
        return bytes(out)


def _is_string_block(payload: bytes) -> bool:
    """Any UTF-16 NUL-terminated block (including empty / non-text strings)."""
    body = payload.rstrip(b"@")
    if len(body) % 2:
        body += b"@"
    if len(body) < 2 or body[-2:] != b"\0\0" or len(payload) - len(body) > 3:
        return False
    for i in range(0, len(body) - 2, 2):
        if body[i] == 0 and body[i + 1] == 0:
            return False
    return True


def _structural_refs(n: int, words, block_size: dict[int, int], strings: dict,
                     stringish: set | None = None, ambiguous: set | None = None):
    """Find string references by walking the serialized object graph from the root.

    Arrays are (u32 count, u32 relptr) pairs whose target block size is a multiple of
    count; that yields the element size. A column (field offset) of an array is a string
    pointer only if *every* non-zero value in that column points exactly at a string
    block - a flags field like 0x0000FF00 that hits a string start by coincidence in one
    element fails this test in the others.
    """
    def u32(p: int):
        if p < 0 or p + 4 > n or p & 3:
            return None
        return words[p >> 2]

    def rel(p: int):
        v = u32(p)
        if not v:
            return None
        return (p + v) & 0xFFFFFFFF

    stringish = stringish if stringish is not None else set(strings)
    ambiguous = ambiguous or set()
    refs: dict[int, list[int]] = {}
    queue: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def push_array(base: int, count: int):
        size = block_size.get(base)
        if size is None or count <= 0 or size % count or (size // count) % 4 or size == 0:
            return False
        queue.append((base, count, size // count))
        return True

    root_count, root_target = u32(0x20), rel(0x24)
    if root_count and root_target is not None:
        push_array(root_target, root_count)

    while queue:
        base, count, esize = queue.pop()
        if (base, esize) in seen:
            continue
        seen.add((base, esize))
        k = 0
        while k < esize:
            col = [base + i * esize + k for i in range(count)]
            # (count, relptr) array descriptor spanning k and k+4
            if k + 8 <= esize:
                ok, targets = True, []
                for p in col:
                    c, v = u32(p), u32(p + 4)
                    if c == 0 and v == 0:
                        continue
                    if not c or not v or c > 0x100000:
                        ok = False
                        break
                    t = (p + 4 + v) & 0xFFFFFFFF
                    size = block_size.get(t)
                    if (t in stringish and t not in ambiguous) or size is None or size % c or (size // c) % 4 or size == 0:
                        ok = False
                        break
                    targets.append((t, c))
                if ok and targets:
                    for t, c in targets:
                        push_array(t, c)
                    k += 8
                    continue
            vals = [(p, rel(p)) for p in col]
            nonzero = [(p, t) for p, t in vals if t is not None]
            if nonzero and all(t in stringish for _, t in nonzero):
                for p, t in nonzero:
                    if t in strings:
                        refs.setdefault(t, []).append(p)
            elif nonzero and all(t in block_size and t not in stringish for _, t in nonzero):
                for _, t in nonzero:
                    push_array(t, 1)
            k += 4
    return refs, {base for base, _ in seen}


_TEXT_RANGES = (
    (0x20, 0x7F), (0xA0, 0x100),            # ASCII, Latin-1
    (0x2000, 0x2070), (0x2100, 0x27C0),     # punctuation, letterlike, arrows, shapes, dingbats
    (0x3000, 0x3100), (0x3200, 0x3400),     # CJK symbols, kana, enclosed/compat CJK
    (0x4E00, 0xA000),                       # CJK unified ideographs
    (0xE000, 0xF000),                       # private use (game icons)
    (0xFF00, 0xFFF0),                       # full/half width forms
)


def is_text_char(o: int) -> bool:
    for a, b in _TEXT_RANGES:
        if a <= o < b:
            return True
    return False


def valid_text(s: str) -> bool:
    """Heuristic: does s look like game text (allowing the game's control codes)?"""
    printable = 0
    i = 0
    while i < len(s):
        o = ord(s[i])
        if o == 0x04:          # variable placeholder: 0x04 + one parameter char
            i += 2
            continue
        if o in (0x01, 0x02, 0x03, 0x09, 0x0A, 0x0D):
            pass
        elif o < 0x20 or not is_text_char(o):
            return False
        else:
            printable += 1
        i += 1
    return printable > 0


def _as_string(payload: bytes, at_eof: bool = False) -> bytes | None:
    """Return the UTF-16 content if payload is a single NUL-terminated string block."""
    if len(payload) < 4 or (len(payload) % 4 and not at_eof):
        return None
    # strip '@' padding (only allowed after the terminator)
    body = payload.rstrip(b"@")
    if len(body) % 2:
        body += b"@"  # odd strip ate half of a char: undo
    if len(body) < 2 or body[-2:] != b"\0\0":
        return None
    if len(payload) - len(body) > 2:
        return None
    content = body[:-2]
    if len(content) % 2:
        return None
    # no embedded NULs
    for i in range(0, len(content), 2):
        if content[i] == 0 and content[i + 1] == 0:
            return None
    if not content:
        return None
    try:
        s = content.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    return content if valid_text(s) else None
