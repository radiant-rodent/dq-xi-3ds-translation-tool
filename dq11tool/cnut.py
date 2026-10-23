"""String literals in compiled Squirrel 3.0 scripts (.cnut).

The serialized closure is a flat stream; string objects are written as
  u32 type (OT_STRING = 0x08000010), u32 length, bytes (UTF-8, no terminator)
with no absolute offsets anywhere, so a string can be replaced by one of a
different length by rewriting its length prefix and bytes.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

OT_STRING = b"\x10\x00\x00\x08"
_PAT = re.compile(re.escape(OT_STRING) + b"(....)", re.S)


@dataclass
class CnutString:
    offset: int     # offset of the type tag
    text: str


def find_strings(data: bytes) -> list[CnutString]:
    if data[:2] != b"\xfa\xfa":
        return []
    out = []
    pos = 0
    while True:
        m = _PAT.search(data, pos)
        if not m:
            break
        n = struct.unpack("<I", m.group(1))[0]
        start = m.end()
        if 0 < n < 0x10000 and start + n <= len(data):
            try:
                s = data[start:start + n].decode("utf-8")
                out.append(CnutString(m.start(), s))
                pos = start + n
                continue
            except UnicodeDecodeError:
                pass
        pos = m.start() + 1
    return out


def replace_strings(data: bytes, repl: dict[int, str]) -> bytes:
    """repl: tag offset -> new text."""
    out = bytearray()
    last = 0
    for off in sorted(repl):
        n = struct.unpack_from("<I", data, off + 4)[0]
        out += data[last:off]
        b = repl[off].encode("utf-8")
        out += OT_STRING + struct.pack("<I", len(b)) + b
        last = off + 8 + n
    out += data[last:]
    return bytes(out)
