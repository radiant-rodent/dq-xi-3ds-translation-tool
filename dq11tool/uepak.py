"""Minimal Unreal Engine 4 .pak reader (pak versions 1-11, zlib, optional AES-256 index/data).

Only what is needed to pull text assets out of the PC version: list files and read one.
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

MAGIC = 0x5A6F12E1


@dataclass
class PakEntry:
    name: str
    offset: int
    size: int            # stored size
    usize: int           # uncompressed size
    method: int          # 0 none, 1 zlib (index into compression names for v8+)
    blocks: list         # [(start, end)] absolute or entry-relative offsets
    encrypted: bool
    block_size: int


def _fstring(b: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from("<i", b, p)[0]
    p += 4
    if n == 0:
        return "", p
    if n < 0:
        raw = b[p:p - n * 2]
        return raw.decode("utf-16-le").rstrip("\0"), p - n * 2
    return b[p:p + n].decode("utf-8", "replace").rstrip("\0"), p + n


# Dragon Quest XI (PC, 2018) obfuscates its pak: file names are XORed with this 8-byte
# key, compressed blocks with its bitwise complement (restarting at every block) and
# stored (uncompressed) data is bitwise inverted.
DQXI_KEY = bytes.fromhex("DEADFADEBEEFCAFE")


def _xor(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    k = (key * (len(data) // len(key) + 1))[:len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(k, "little")).to_bytes(len(data), "little")


class Pak:
    def __init__(self, path: str, aes_key: bytes | None = None, xor_key: bytes | None = None):
        self.path = path
        self.key = aes_key
        self.name_key = xor_key or b""
        self.block_key = bytes(b ^ 0xFF for b in xor_key) if xor_key else b""
        self.raw_key = bytes([0xFF]) if xor_key else b""
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        self.file_size = self.f.tell()
        self._read_footer()
        self._read_index()

    def close(self):
        self.f.close()

    def _decrypt(self, data: bytes) -> bytes:
        if not self.key:
            raise RuntimeError(f"{os.path.basename(self.path)} is encrypted and no AES key is known")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        return d.update(data) + d.finalize()

    def _read_footer(self):
        # try the footer layouts of the different pak versions, newest first
        for size in (221 + 1, 221, 189, 61, 45, 44):
            if size > self.file_size:
                continue
            self.f.seek(self.file_size - size)
            ft = self.f.read(size)
            for magic_at in (0, 1, 17):          # v<4: 0, v4-6: after encrypted flag, v7+: after guid+flag
                if magic_at + 44 > len(ft):
                    continue
                magic, ver, ioff, isz = struct.unpack_from("<IIqq", ft, magic_at)
                if magic == MAGIC and 1 <= ver <= 11 and 0 < ioff < self.file_size and 0 < isz < self.file_size:
                    self.version = ver
                    self.index_offset, self.index_size = ioff, isz
                    self.index_encrypted = magic_at >= 1 and ft[magic_at - 1] == 1
                    names = []
                    tail = ft[magic_at + 44:]
                    if ver >= 9:
                        tail = tail[1:]          # frozen index flag
                    if ver >= 8:
                        for i in range(0, len(tail) - 31, 32):
                            nm = tail[i:i + 32].split(b"\0", 1)[0].decode("ascii", "replace")
                            if nm:
                                names.append(nm)
                    self.compression_names = names
                    return
        raise ValueError(f"{self.path}: not a UE4 pak (no footer found)")

    def _entry(self, b: bytes, p: int) -> tuple[PakEntry, int]:
        off, size, usize = struct.unpack_from("<qqq", b, p)
        p += 24
        if self.version >= 8 and len(self.compression_names) <= 4 and False:
            pass
        method = struct.unpack_from("<I", b, p)[0]
        p += 4
        if self.version <= 1:
            p += 8                                # timestamp
        p += 20                                   # sha1
        blocks = []
        if self.version >= 3:
            if method != 0:
                n = struct.unpack_from("<I", b, p)[0]
                p += 4
                for _ in range(n):
                    s, e = struct.unpack_from("<qq", b, p)
                    p += 16
                    blocks.append((s, e))
            enc = b[p] != 0
            bs = struct.unpack_from("<I", b, p + 1)[0]
            p += 5
        else:
            enc, bs = False, 0
        return PakEntry("", off, size, usize, method, blocks, enc, bs), p

    def _read_index(self):
        self.f.seek(self.index_offset)
        b = self.f.read(self.index_size)
        if self.index_encrypted:
            b = self._decrypt(b)
        self.mount, p = _fstring(b, 0)
        if self.version >= 10:
            raise NotImplementedError("pak version 10+ path-hash index is not supported")
        count = struct.unpack_from("<i", b, p)[0]
        p += 4
        self.entries: dict[str, PakEntry] = {}
        for _ in range(count):
            name, p = _fstring(b, p)
            if self.name_key:
                name = "".join(chr(ord(c) ^ self.name_key[i % len(self.name_key)]) for i, c in enumerate(name)).rstrip("\0")
            e, p = self._entry(b, p)
            e.name = name
            self.entries[name] = e

    def names(self) -> list[str]:
        return list(self.entries)

    def read(self, name: str) -> bytes:
        e = self.entries[name]
        # each entry's data is preceded by a copy of its record
        hdr = self._header_size(e)
        if e.method == 0:
            self.f.seek(e.offset + hdr)
            data = self.f.read(_align16(e.size) if e.encrypted else e.size)
            if e.encrypted:
                data = self._decrypt(data)
            return _xor(data[:e.size], self.raw_key)
        out = bytearray()
        base = e.offset if self.version >= 5 else 0   # v5+: block offsets relative to the entry
        for s, end in e.blocks:
            self.f.seek(base + s)
            n = end - s
            chunk = self.f.read(_align16(n) if e.encrypted else n)
            if e.encrypted:
                chunk = self._decrypt(chunk)[:n]
            out += zlib.decompress(_xor(chunk, self.block_key))
        return bytes(out[:e.usize])

    def _header_size(self, e: PakEntry) -> int:
        n = 24 + 4 + 20 + (8 if self.version <= 1 else 0)
        if self.version >= 3:
            if e.method != 0:
                n += 4 + 16 * len(e.blocks)
            n += 5
        return n


def _align16(n: int) -> int:
    return (n + 15) & ~15


def open_paks(root: str, aes_key: bytes | None = None, xor_key: bytes | None = None) -> list[Pak]:
    paks = []
    for dp, _, fn in os.walk(root):
        for n in sorted(fn):
            if n.lower().endswith(".pak"):
                paks.append(Pak(os.path.join(dp, n), aes_key, xor_key))
    return paks
