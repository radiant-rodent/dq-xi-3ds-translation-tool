"""Parsers for decrypted 3DS containers.

Layout reference (3dbrew):
  CCI/NCSD  -> up to 8 NCCH partitions (0 = game, 1 = manual, 6/7 = update data)
  NCCH      -> header, ExHeader, logo, plain region, ExeFS, RomFS
  RomFS     -> IVFC hash tree wrapping a "level 3" filesystem image
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

MEDIA_UNIT = 0x200


class Window:
    """A read-only view onto a region of a file."""

    def __init__(self, f, offset: int, size: int):
        self.f = f
        self.offset = offset
        self.size = size

    def read(self, off: int, size: int) -> bytes:
        if off < 0 or off + size > self.size:
            raise ValueError(f"read out of window: {off:#x}+{size:#x} > {self.size:#x}")
        self.f.seek(self.offset + off)
        return self.f.read(size)

    def sub(self, off: int, size: int) -> "Window":
        return Window(self.f, self.offset + off, size)


# --------------------------------------------------------------------------- NCSD

@dataclass
class NcsdPartition:
    index: int
    offset: int  # bytes, absolute in file
    size: int    # bytes
    fs_type: int
    crypt_type: int
    partition_id: int


@dataclass
class Ncsd:
    header: bytes                   # first 0x4000 bytes (NCSD header + card info + padding)
    image_size: int
    media_id: int
    partitions: list[NcsdPartition]

    @classmethod
    def parse(cls, f) -> "Ncsd":
        f.seek(0)
        header = f.read(0x4000)
        if header[0x100:0x104] != b"NCSD":
            raise ValueError("not an NCSD (.3ds/.cci) image")
        image_size = struct.unpack_from("<I", header, 0x104)[0] * MEDIA_UNIT
        media_id = struct.unpack_from("<Q", header, 0x108)[0]
        parts = []
        for i in range(8):
            off, size = struct.unpack_from("<II", header, 0x120 + i * 8)
            if size == 0:
                continue
            pid = struct.unpack_from("<Q", header, 0x190 + i * 8)[0]
            parts.append(NcsdPartition(i, off * MEDIA_UNIT, size * MEDIA_UNIT,
                                       header[0x110 + i], header[0x118 + i], pid))
        return cls(header, image_size, media_id, parts)

    def partition(self, index: int) -> NcsdPartition | None:
        for p in self.partitions:
            if p.index == index:
                return p
        return None

    @property
    def used_size(self) -> int:
        return max(p.offset + p.size for p in self.partitions)


# --------------------------------------------------------------------------- NCCH

@dataclass
class Ncch:
    win: Window
    header: bytes  # 0x200 bytes (signature + header)
    content_size: int
    partition_id: int
    program_id: int
    product_code: str
    exheader_size: int
    flags: bytes
    plain_offset: int
    plain_size: int
    logo_offset: int
    logo_size: int
    exefs_offset: int
    exefs_size: int
    exefs_hash_size: int
    romfs_offset: int
    romfs_size: int
    romfs_hash_size: int

    @classmethod
    def parse(cls, win: Window) -> "Ncch":
        h = win.read(0, 0x200)
        if h[0x100:0x104] != b"NCCH":
            raise ValueError("not an NCCH partition")
        u32 = lambda o: struct.unpack_from("<I", h, o)[0]
        n = cls(
            win=win, header=h,
            content_size=u32(0x104) * MEDIA_UNIT,
            partition_id=struct.unpack_from("<Q", h, 0x108)[0],
            program_id=struct.unpack_from("<Q", h, 0x118)[0],
            product_code=h[0x150:0x160].rstrip(b"\0").decode("ascii", "replace"),
            exheader_size=u32(0x180),
            flags=h[0x188:0x190],
            plain_offset=u32(0x190) * MEDIA_UNIT, plain_size=u32(0x194) * MEDIA_UNIT,
            logo_offset=u32(0x198) * MEDIA_UNIT, logo_size=u32(0x19C) * MEDIA_UNIT,
            exefs_offset=u32(0x1A0) * MEDIA_UNIT, exefs_size=u32(0x1A4) * MEDIA_UNIT,
            exefs_hash_size=u32(0x1A8) * MEDIA_UNIT,
            romfs_offset=u32(0x1B0) * MEDIA_UNIT, romfs_size=u32(0x1B4) * MEDIA_UNIT,
            romfs_hash_size=u32(0x1B8) * MEDIA_UNIT,
        )
        return n

    @property
    def is_decrypted(self) -> bool:
        return bool(self.flags[7] & 0x04)

    def exheader(self) -> bytes:
        return self.win.read(0x200, 0x800 if self.exheader_size else 0)

    def exefs(self) -> "ExeFs | None":
        if not self.exefs_size:
            return None
        return ExeFs.parse(self.win.sub(self.exefs_offset, self.exefs_size))

    def romfs(self) -> "RomFs | None":
        if not self.romfs_size:
            return None
        return RomFs.parse(self.win.sub(self.romfs_offset, self.romfs_size))


# --------------------------------------------------------------------------- ExeFS

@dataclass
class ExeFsEntry:
    name: str
    offset: int  # relative to end of 0x200 header
    size: int


@dataclass
class ExeFs:
    win: Window
    entries: list[ExeFsEntry]

    @classmethod
    def parse(cls, win: Window) -> "ExeFs":
        h = win.read(0, 0x200)
        entries = []
        for i in range(10):
            name, off, size = struct.unpack_from("<8sII", h, i * 16)
            name = name.rstrip(b"\0").decode("ascii", "replace")
            if name:
                entries.append(ExeFsEntry(name, off, size))
        return cls(win, entries)

    def read(self, name: str) -> bytes:
        for e in self.entries:
            if e.name == name:
                return self.win.read(0x200 + e.offset, e.size)
        raise KeyError(name)


# --------------------------------------------------------------------------- RomFS

@dataclass
class IvfcLevel:
    logical_offset: int
    size: int
    block_log2: int


@dataclass
class RomFsFile:
    path: str        # "/a/b/c.bin"
    offset: int      # relative to level-3 file data region
    size: int
    meta_off: int = 0  # offset of this file's entry within level 3 (for rebuilding)


@dataclass
class RomFsDir:
    path: str
    dirs: list["RomFsDir"] = field(default_factory=list)
    files: list[RomFsFile] = field(default_factory=list)


def _align(x: int, a: int) -> int:
    return (x + a - 1) // a * a


@dataclass
class RomFs:
    win: Window
    master_hash_size: int
    levels: list[IvfcLevel]
    level3_offset: int          # within RomFS window
    data_offset: int            # file-data start, within level 3
    root: RomFsDir
    files: list[RomFsFile]      # in on-disk metadata order
    ivfc_header: bytes = b""     # first 0x60 bytes

    @classmethod
    def parse(cls, win: Window) -> "RomFs":
        h = win.read(0, 0x60)
        if h[:4] != b"IVFC" or struct.unpack_from("<I", h, 4)[0] != 0x10000:
            raise ValueError("bad IVFC header")
        mhs = struct.unpack_from("<I", h, 8)[0]
        levels = []
        for i in range(3):
            lo, sz, bl = struct.unpack_from("<QQI", h, 0x0C + i * 0x18)
            levels.append(IvfcLevel(lo, sz, bl))
        l3_off = _align(0x60 + mhs, 1 << levels[2].block_log2)
        l3 = win.sub(l3_off, levels[2].size)
        lh = l3.read(0, 0x28)
        (hdr_size, dht_off, dht_size, dmt_off, dmt_size,
         fht_off, fht_size, fmt_off, fmt_size, data_off) = struct.unpack("<10I", lh)
        dmt = l3.read(dmt_off, dmt_size)
        fmt = l3.read(fmt_off, fmt_size)
        files: list[RomFsFile] = []

        def name_at(buf, off, base):
            nlen = struct.unpack_from("<I", buf, off + base)[0]
            return buf[off + base + 4: off + base + 4 + nlen].decode("utf-16-le")

        def walk_dir(off: int, path: str) -> RomFsDir:
            parent, sibling, child, first_file, nxt = struct.unpack_from("<5I", dmt, off)
            d = RomFsDir(path)
            f_off = first_file
            while f_off != 0xFFFFFFFF:
                fp, fs, doff, dsize, fnext, = struct.unpack_from("<IIQQI", fmt, f_off)
                fname = name_at(fmt, f_off, 0x1C)
                rf = RomFsFile(f"{path.rstrip('/')}/{fname}", doff, dsize, fmt_off + f_off)
                d.files.append(rf)
                files.append(rf)
                f_off = fs
            c_off = child
            while c_off != 0xFFFFFFFF:
                cname = name_at(dmt, c_off, 0x14)
                sub = walk_dir(c_off, f"{path.rstrip('/')}/{cname}")
                d.dirs.append(sub)
                c_off = struct.unpack_from("<I", dmt, c_off + 4)[0]
            return d

        root = walk_dir(0, "/")
        return cls(win, mhs, levels, l3_off, data_off, root, files, h)

    def read_file(self, rf: RomFsFile, start: int = 0, size: int | None = None) -> bytes:
        if size is None:
            size = rf.size - start
        return self.win.read(self.level3_offset + self.data_offset + rf.offset + start, size)

    def file_abs_offset(self, rf: RomFsFile) -> int:
        """Absolute offset of the file data in the underlying image file."""
        return self.win.offset + self.level3_offset + self.data_offset + rf.offset


def open_image(path: str):
    """Open a .3ds/.cci, returning (fileobj, Ncsd, Ncch for partition 0)."""
    f = open(path, "rb")
    ncsd = Ncsd.parse(f)
    p0 = ncsd.partition(0)
    ncch = Ncch.parse(Window(f, p0.offset, p0.size))
    return f, ncsd, ncch
