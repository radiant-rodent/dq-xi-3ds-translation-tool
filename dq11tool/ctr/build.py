"""Rebuild a decrypted .3ds image with replaced RomFS files and/or ExeFS contents.

The RomFS directory/file metadata is reused unchanged (same names and order); only file
data offsets/sizes are rewritten. The IVFC hash tree, NCCH superblock hashes and the NCSD
partition table are regenerated. Signatures are left as-is (custom firmware and emulators
do not verify them).
"""
from __future__ import annotations

import hashlib
import os
import struct
from typing import Callable

from .formats import MEDIA_UNIT, ExeFs, Ncch, Ncsd, RomFs, Window, open_image

BLOCK = 0x1000
Progress = Callable[[str, int, int], None]


def _align(x: int, a: int) -> int:
    return (x + a - 1) // a * a


class _HashingWriter:
    """Writes a byte stream to a file while collecting SHA-256 of each 0x1000 block."""

    def __init__(self, fp):
        self.fp = fp
        self.hashes = bytearray()
        self.pending = bytearray()
        self.written = 0

    def write(self, data):
        self.fp.write(data)
        self.written += len(data)
        mv = memoryview(data)
        if self.pending:
            need = BLOCK - len(self.pending)
            self.pending += mv[:need]
            mv = mv[need:]
            if len(self.pending) == BLOCK:
                self.hashes += hashlib.sha256(self.pending).digest()
                self.pending = bytearray()
            else:
                return
        full = len(mv) // BLOCK * BLOCK
        sha = hashlib.sha256
        h = self.hashes
        for i in range(0, full, BLOCK):
            h += sha(mv[i:i + BLOCK]).digest()
        if full < len(mv):
            self.pending += mv[full:]

    def finish(self):
        """Pad the stream to a block boundary with zeros (padding is written and hashed)."""
        if self.pending:
            pad = BLOCK - len(self.pending)
            self.fp.write(b"\0" * pad)
            self.written += pad
            self.pending += b"\0" * pad
            self.hashes += hashlib.sha256(self.pending).digest()
            self.pending = bytearray()


def _hash_level(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), BLOCK):
        blk = data[i:i + BLOCK]
        if len(blk) < BLOCK:
            blk = blk + b"\0" * (BLOCK - len(blk))
        out += hashlib.sha256(blk).digest()
    return bytes(out)


def build_exefs(exefs: ExeFs, replace: dict[str, bytes]) -> bytes:
    header = bytearray(0x200)
    data = bytearray()
    for i, e in enumerate(exefs.entries):
        content = replace.get(e.name)
        if content is None:
            content = exefs.read(e.name)
        off = len(data)
        struct.pack_into("<8sII", header, i * 16, e.name.encode("ascii"), off, len(content))
        h = hashlib.sha256(content).digest()
        pos = 0x1E0 - i * 0x20
        header[pos:pos + 0x20] = h
        data += content
        data += b"\0" * (_align(len(data), MEDIA_UNIT) - len(data))
    return bytes(header) + bytes(data)


def write_romfs(out, romfs: RomFs, replacements: dict[str, bytes], progress: Progress) -> int:
    """Write a complete RomFS at the current position of `out`. Returns its size."""
    start = out.tell()
    l3_src = romfs.win.sub(romfs.level3_offset, romfs.levels[2].size)
    data_off = romfs.data_offset
    meta = bytearray(l3_src.read(0, data_off))

    order = sorted(romfs.files, key=lambda f: f.offset)
    layout = []
    pos = 0
    for f in order:
        new = replacements.get(f.path)
        size = len(new) if new is not None else f.size
        layout.append((f, pos, size, new))
        struct.pack_into("<QQ", meta, f.meta_off + 8, pos, size)
        pos = _align(pos + size, 0x10)
    l3_size = data_off + (layout[-1][1] + layout[-1][2] if layout else 0)

    # placeholder for IVFC header + master hash (filled in at the end)
    out.write(b"\0" * BLOCK)
    w = _HashingWriter(out)
    w.write(bytes(meta))
    cur = 0  # position within file data region
    total = len(layout)
    copied = 0
    for i, (f, off, size, new) in enumerate(layout):
        if off > cur:
            w.write(b"\0" * (off - cur))
            cur = off
        if new is not None:
            w.write(new)
        else:
            remaining, p = size, 0
            while remaining:
                n = min(remaining, 8 << 20)
                w.write(romfs.read_file(f, p, n))
                p += n
                remaining -= n
        cur += size
        copied += size
        if i % 256 == 0:
            progress("Writing RomFS", i, total)
    w.finish()
    progress("Writing RomFS", total, total)

    level2 = bytes(w.hashes)
    level1 = _hash_level(level2)
    master = _hash_level(level1)
    # level 1 then level 2, each block aligned
    out.write(level1)
    out.write(b"\0" * (_align(len(level1), BLOCK) - len(level1)))
    out.write(level2)
    out.write(b"\0" * (_align(len(level2), BLOCK) - len(level2)))
    end = out.tell()

    l1_log = 0
    l2_log = _align(len(level1), BLOCK)
    l3_log = _align(l2_log + len(level2), BLOCK)
    hdr = bytearray(romfs.ivfc_header)
    struct.pack_into("<4sII", hdr, 0, b"IVFC", 0x10000, len(master))
    struct.pack_into("<QQI", hdr, 0x0C, l1_log, len(level1), 12)
    struct.pack_into("<QQI", hdr, 0x24, l2_log, len(level2), 12)
    struct.pack_into("<QQI", hdr, 0x3C, l3_log, l3_size, 12)
    if 0x60 + len(master) > BLOCK:
        raise ValueError("master hash too large")
    out.seek(start)
    out.write(bytes(hdr) + master)
    out.seek(end)
    return end - start


def build_image(src_path: str, out_path: str, romfs_replacements: dict[str, bytes],
                exefs_replacements: dict[str, bytes] | None = None,
                progress: Progress | None = None, keep_update_partitions: bool = True):
    progress = progress or (lambda *a: None)
    exefs_replacements = exefs_replacements or {}
    f, ncsd, ncch = open_image(src_path)
    try:
        romfs = ncch.romfs()
        exefs = ncch.exefs()
        p0 = ncsd.partition(0)
        tmp = out_path + ".part"
        with open(tmp, "w+b") as out:
            out.write(ncsd.header)                      # 0x4000, patched later
            ncch_start = out.tell()
            assert ncch_start == p0.offset
            # NCCH header, exheader, logo, plain region: copied verbatim
            out.write(ncch.win.read(0, ncch.exefs_offset))
            if exefs_replacements:
                exefs_bytes = build_exefs(exefs, exefs_replacements)
            else:
                exefs_bytes = ncch.win.read(ncch.exefs_offset, ncch.exefs_size)
            out.write(exefs_bytes)
            exefs_size = _align(len(exefs_bytes), MEDIA_UNIT)
            out.write(b"\0" * (exefs_size - len(exefs_bytes)))
            romfs_off = _align(ncch.exefs_offset + exefs_size, BLOCK)
            out.write(b"\0" * (ncch_start + romfs_off - out.tell()))
            romfs_size = write_romfs(out, romfs, romfs_replacements, progress)
            romfs_size_aligned = _align(romfs_size, MEDIA_UNIT)
            out.write(b"\0" * (romfs_size_aligned - romfs_size))
            ncch_size = romfs_off + romfs_size_aligned

            # NCCH header patch
            hdr = bytearray(ncch.header)
            struct.pack_into("<I", hdr, 0x104, ncch_size // MEDIA_UNIT)
            struct.pack_into("<II", hdr, 0x1A0, ncch.exefs_offset // MEDIA_UNIT, exefs_size // MEDIA_UNIT)
            hdr[0x1C0:0x1E0] = hashlib.sha256(exefs_bytes[:ncch.exefs_hash_size]).digest()
            struct.pack_into("<II", hdr, 0x1B0, romfs_off // MEDIA_UNIT, romfs_size_aligned // MEDIA_UNIT)
            out.seek(ncch_start + romfs_off)
            ivfc = out.read(0x60)
            mhs = struct.unpack_from("<I", ivfc, 8)[0]
            hash_units = _align(0x60 + mhs, MEDIA_UNIT) // MEDIA_UNIT
            out.seek(ncch_start + romfs_off)
            superblock = out.read(hash_units * MEDIA_UNIT)
            struct.pack_into("<I", hdr, 0x1B8, hash_units)
            hdr[0x1E0:0x200] = hashlib.sha256(superblock).digest()
            out.seek(ncch_start)
            out.write(hdr)

            # remaining partitions
            out.seek(ncch_start + ncch_size)
            table = {0: (p0.offset, ncch_size)}
            pos = ncch_start + ncch_size
            for p in ncsd.partitions:
                if p.index == 0:
                    continue
                if not keep_update_partitions and p.index in (6, 7):
                    continue
                pos = _align(pos, MEDIA_UNIT)
                out.seek(pos)
                table[p.index] = (pos, p.size)
                remaining, q = p.size, p.offset
                while remaining:
                    n = min(remaining, 8 << 20)
                    f.seek(q)
                    out.write(f.read(n))
                    q += n
                    remaining -= n
                pos += p.size
            used = pos

            # NCSD header patch
            nh = bytearray(ncsd.header)
            for i in range(8):
                off, size = table.get(i, (0, 0))
                struct.pack_into("<II", nh, 0x120 + i * 8, off // MEDIA_UNIT, size // MEDIA_UNIT)
                if i not in table:
                    struct.pack_into("<Q", nh, 0x190 + i * 8, 0)
            if used > ncsd.image_size:
                raise ValueError("rebuilt image exceeds cartridge capacity")
            struct.pack_into("<I", nh, 0x300, used)
            out.seek(0)
            out.write(nh)
            out.truncate(used)
        if os.path.exists(out_path):
            os.remove(out_path)
        os.replace(tmp, out_path)
        progress("Image written", 1, 1)
    finally:
        f.close()


def verify_image(path: str) -> list[str]:
    """Re-check hashes of a built image. Returns a list of problems (empty = OK)."""
    problems = []
    f, ncsd, ncch = open_image(path)
    try:
        exefs_hdr = ncch.win.read(ncch.exefs_offset, ncch.exefs_hash_size)
        if hashlib.sha256(exefs_hdr).digest() != ncch.header[0x1C0:0x1E0]:
            problems.append("ExeFS superblock hash mismatch")
        ex = ncch.exefs()
        for i, e in enumerate(ex.entries):
            if hashlib.sha256(ex.read(e.name)).digest() != exefs_hdr[0x1E0 - i * 0x20:0x200 - i * 0x20]:
                problems.append(f"ExeFS hash mismatch for {e.name}")
        sb = ncch.win.read(ncch.romfs_offset, ncch.romfs_hash_size)
        if hashlib.sha256(sb).digest() != ncch.header[0x1E0:0x200]:
            problems.append("RomFS superblock hash mismatch")
        r = ncch.romfs()
        mh = r.win.read(0x60, r.master_hash_size)
        l1_off = _align(r.level3_offset + r.levels[2].size, BLOCK)
        l1 = r.win.read(l1_off, r.levels[0].size)
        if _hash_level(l1) != mh:
            problems.append("RomFS master hash mismatch")
        l2_off = _align(l1_off + r.levels[0].size, BLOCK)
        l2 = r.win.read(l2_off, r.levels[1].size)
        if _hash_level(l2) != l1:
            problems.append("RomFS level 1 hash mismatch")
        # spot-check level 3 blocks
        nblocks = len(l2) // 32
        step = max(1, nblocks // 2000)
        for b in range(0, nblocks, step):
            blk = r.win.read(r.level3_offset + b * BLOCK, BLOCK)
            if hashlib.sha256(blk).digest() != l2[b * 32:b * 32 + 32]:
                problems.append(f"RomFS level 3 block {b} hash mismatch")
                break
    finally:
        f.close()
    return problems
