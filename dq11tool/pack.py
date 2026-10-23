"""DQXI 3DS PACK / PACA archive format.

PACK layout (little endian):
  0x00 "PACK"
  0x04 u32 total size
  0x08 u16 version (0x0100)   0x0A u16 BOM (0xFEFF)
  0x0C u16 entry count        0x0E u16 header size (0x18)
  0x10 u32 unknown (alignment/flags)   0x14 u32 hash
  0x18 u32[count] entry offsets
       u32[count*2] name hashes
  entries: u16 entry header size, u16 flags, u32 data size, name (NUL padded), data
           entries are 4-byte aligned

PACA = "PACA" + u32 decompressed size + BLZ-compressed remainder of the PACK file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import blz

MAX_ALIGN = 0x80


@dataclass
class PackEntry:
    name: str
    data: bytes
    flags: int
    header_size: int
    raw_header: bytes  # original entry header (incl. name field), size header_size
    align: int = 4     # payload alignment in the original file (capped at MAX_ALIGN)


@dataclass
class Pack:
    compressed: bool
    header: bytes            # bytes 0x08..0x18 of the original header
    hashes: bytes            # name hash table, count*8 bytes
    entries: list[PackEntry] = field(default_factory=list)
    tail: bytes = b""        # any trailing bytes after the last entry

    @staticmethod
    def is_pack(data: bytes) -> bool:
        return data[:4] in (b"PACK", b"PACA")

    @classmethod
    def parse(cls, data: bytes) -> "Pack":
        magic = data[:4]
        compressed = magic == b"PACA"
        if compressed:
            data = blz.decompress(data)
        elif magic != b"PACK":
            raise ValueError("not a pack")
        total, = struct.unpack_from("<I", data, 4)
        count, hsize = struct.unpack_from("<HH", data, 0x0C)
        offs = struct.unpack_from(f"<{count}I", data, 0x18)
        hashes = data[0x18 + 4 * count: 0x18 + 12 * count]
        p = cls(compressed, data[8:0x18], hashes)
        end = 0
        for o in offs:
            eh, fl, sz = struct.unpack_from("<HHI", data, o)
            raw_header = data[o:o + eh]
            name = raw_header[8:].split(b"\0", 1)[0].decode("ascii", "replace")
            p.entries.append(PackEntry(name, data[o + eh:o + eh + sz], fl, eh, raw_header,
                                       max(4, min((o + eh) & -(o + eh), MAX_ALIGN))))
            end = max(end, o + eh + sz)
        p.tail = data[_align4(end):total] if _align4(end) < total else b""
        return p

    def build(self, compress: bool | None = None) -> bytes:
        if compress is None:
            compress = self.compressed
        count = len(self.entries)
        out = bytearray(b"PACK\0\0\0\0")
        out += self.header
        struct.pack_into("<H", out, 0x0C, count)
        out += b"\0" * (4 * count)
        out += self.hashes
        offs = []
        for e in self.entries:
            # Textures (BCH, SARC layouts) are used in place and need their original 0x80
            # alignment; payloads packed at 4 bytes after a grown text entry made the game
            # get null textures (crash entering the first battle).
            while len(out) % 4 or (len(out) + e.header_size) % e.align:
                out += b"\0"
            offs.append(len(out))
            hdr = bytearray(e.raw_header)
            struct.pack_into("<I", hdr, 4, len(e.data))
            out += hdr
            out += e.data
        while len(out) % 4:
            out += b"\0"
        out += self.tail
        struct.pack_into(f"<{count}I", out, 0x18, *offs)
        struct.pack_into("<I", out, 4, len(out))
        data = bytes(out)
        if compress:
            c = blz.compress(data[8:])
            if c is not None:
                return b"PACA" + struct.pack("<I", len(data)) + c
        return data

    def get(self, name: str) -> PackEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None


def _align4(x: int) -> int:
    return (x + 3) & ~3
