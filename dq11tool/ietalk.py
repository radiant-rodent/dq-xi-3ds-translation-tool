"""Split NPC talk lines into several message boxes ({PAGE} in a translation).

The message window shows one string per box, at most 3 lines (gamefont.MESSAGE_LINES); a
Japanese NPC who says more simply has more lines in the talk. CharacterIEData (npc_ie.bxon)
stores every talk as an array of 28-byte line records:

    +0x00 u32 count, +0x04 relptr   optional per-line data (gesture / camera commands)
    +0x08 u32 flags                 +0x0C u32 index of the line in the talk
    +0x10 relptr speaker name       +0x14 relptr text            +0x18 u32 (0)

Tree: root[] (24 B) +0x08 -> talks[] (16 B) +0x00 -> variants[] (12 B) +0x00 -> lines[].
A line whose text contains U+001F (textcodec.PAGE) becomes several consecutive records: the
first keeps everything, the others get the same speaker and flags and no per-line data. The
new line array and the new strings are appended to the file, the variant's (count, relptr)
is re-pointed and the indexes are renumbered.

Party chat (TalkPartyContent2) works the same way one level simpler: every utterance already
is an array of strings shown as consecutive boxes (split_party).
"""
from __future__ import annotations

import struct

from .bxon import MARK
from .textcodec import PAGE

LINE = 28
_PAGE16 = PAGE.encode("utf-16-le")


def _u32(d, p):
    return struct.unpack_from("<I", d, p)[0]


def _target(d, p):
    v = _u32(d, p)
    return (p + v) & 0xFFFFFFFF if v else None


def _string_at(d, p) -> bytes:
    """UTF-16 payload (without NUL) of the string block at p."""
    end = p
    while d[end:end + 2] != b"\0\0":
        end += 2
    return bytes(d[p:end])


def _append(out: bytearray, payload: bytes) -> int:
    while len(out) % 4:
        out += b"@"
    out += MARK
    at = len(out)
    out += payload
    while len(out) % 4:
        out += b"@"
    return at


def _string_payload(raw: bytes) -> bytes:
    p = raw + b"\0\0"
    return p + b"@" * (-len(p) % 4)


def _line_arrays(d: bytes):
    """(descriptor position, count, array start) of every talk's line array."""
    if d[0x0C:0x20].split(b"\0", 1)[0] != b"CharacterIEData":
        return
    rc = _u32(d, 0x20)
    root = _target(d, 0x24)
    for i in range(rc):
        e = root + 24 * i
        nt = _u32(d, e + 8)
        talks = _target(d, e + 12)
        if not nt or talks is None:
            continue
        for t in range(nt):
            te = talks + 16 * t
            nv = _u32(d, te)
            variants = _target(d, te + 4)
            if not nv or variants is None:
                continue
            for v in range(nv):
                ve = variants + 12 * v
                nl = _u32(d, ve)
                lines = _target(d, ve + 4)
                if nl and lines is not None:
                    yield ve, nl, lines


def split_party(data: bytes) -> tuple[bytes, int]:
    """Party chat (TalkPartyContent2, talk_content.bxon): root[] of 12-byte utterances
    (u32 count, relptr -> count self-relative string pointers = consecutive boxes, u32 flags).
    A string with U+001F becomes several strings of the same utterance."""
    if data[0x0C:0x20].split(b"\0", 1)[0] != b"TalkPartyContent2":
        return data, 0
    out = bytearray(data)
    extra = 0
    shared: dict[bytes, int] = {}
    rc, root = _u32(data, 0x20), _target(data, 0x24)
    for i in range(rc):
        e = root + 12 * i
        n, arr = _u32(data, e), _target(data, e + 4)
        if not n or arr is None:
            continue
        strings = [_target(data, arr + 4 * k) for k in range(n)]
        parts = [_split16(_string_at(data, s)) if s is not None else [b""] for s in strings]
        if all(len(p) == 1 for p in parts):
            continue
        ats: list[int] = []
        for s, ps in zip(strings, parts):
            if len(ps) == 1:
                ats.append(s)
            else:
                ats += _place(out, data, s, ps, shared)
        while len(out) % 4:
            out += b"@"
        out += MARK
        new_arr = len(out)
        for k, t in enumerate(ats):
            out += struct.pack("<I", (t - (new_arr + 4 * k)) & 0xFFFFFFFF)
        struct.pack_into("<II", out, e, len(ats), (new_arr - (e + 4)) & 0xFFFFFFFF)
        extra += len(ats) - n
    return bytes(out), extra


def split_pages(data: bytes) -> tuple[bytes, int]:
    """Returns (new bxon, number of extra message boxes created)."""
    if PAGE.encode("utf-16-le") not in data:
        return data, 0
    if data[0x0C:0x20].split(b"\0", 1)[0] == b"TalkPartyContent2":
        return split_party(data)
    out = bytearray(data)
    extra = 0
    placed: dict[int, list[int]] = {}       # old text block -> its page strings (shared texts)
    shared: dict[bytes, int] = {}           # page payload -> appended string (same text elsewhere)
    for desc, n, base in list(_line_arrays(data)):
        recs = []
        changed = False
        for i in range(n):
            p = base + LINE * i
            text_at = _target(data, p + 0x14)
            raw = _string_at(data, text_at) if text_at is not None else b""
            parts = _split16(raw)
            if len(parts) > 1:
                changed = True
            recs.append((p, parts))
        if not changed:
            continue
        # new strings first, then the array (relptrs are computed against final positions)
        plan = []
        for p, parts in recs:
            if len(parts) == 1:
                plan.append((p, None, True))
                continue
            text_at = _target(data, p + 0x14)
            if text_at not in placed:
                placed[text_at] = _place(out, data, text_at, parts, shared)
            for k, at in enumerate(placed[text_at]):
                plan.append((p, at, k == 0))
        while len(out) % 4:
            out += b"@"
        out += MARK
        new_base = len(out)
        out += b"\0" * (LINE * len(plan))
        for idx, (src, text_at, first) in enumerate(plan):
            q = new_base + LINE * idx
            rec = bytearray(data[src:src + LINE])
            # self-relative pointers: keep the old targets from the new position
            for f in (0x04, 0x10, 0x14):
                if f == 0x04 and not _u32(data, src):
                    continue
                t = _target(data, src + f)
                if t is not None:
                    struct.pack_into("<I", rec, f, (t - (q + f)) & 0xFFFFFFFF)
            if not first:
                struct.pack_into("<II", rec, 0, 0, 0)       # gestures stay on the first box
            if text_at is not None:
                struct.pack_into("<I", rec, 0x14, (text_at - (q + 0x14)) & 0xFFFFFFFF)
            struct.pack_into("<I", rec, 0x0C, idx)
            out[q:q + LINE] = rec
        struct.pack_into("<II", out, desc, len(plan), (new_base - (desc + 4)) & 0xFFFFFFFF)
        extra += len(plan) - n
    return bytes(out), extra


def _place(out: bytearray, data: bytes, old: int, parts: list[bytes], shared: dict) -> list[int]:
    """Write the page strings of a split text. The old block (the whole text, referenced
    only by split lines from now on) is reused: page strings are packed into it, each
    after its own '&&&&' marker, as long as they fit; the rest is appended."""
    size = len(data[old:].split(MARK, 1)[0]) if MARK in data[old:] else len(data) - old
    pos, end, ats = old, old + size, []
    for k, part in enumerate(parts):
        payload = _string_payload(part)
        need = len(payload) if k == 0 else 4 + len(payload)
        if ats is not None and pos + need <= end:
            if k:
                out[pos:pos + 4] = MARK
                pos += 4
            out[pos:pos + len(payload)] = payload
            ats.append(pos)
            pos += len(payload)
            continue
        if payload not in shared:
            shared[payload] = _append(out, payload)
        ats.append(shared[payload])
    if end - pos >= 8:                      # leftover of the old block: an empty block
        out[pos:pos + 4] = MARK
        out[pos + 4:end] = b"@" * (end - pos - 4)
    elif pos < end:
        out[pos:end] = b"@" * (end - pos)
    return ats


def _split16(raw: bytes) -> list[bytes]:
    parts, cur = [], bytearray()
    for i in range(0, len(raw), 2):
        u = raw[i:i + 2]
        if u == _PAGE16:
            parts.append(bytes(cur))
            cur = bytearray()
        else:
            cur += u
    parts.append(bytes(cur))
    return parts
