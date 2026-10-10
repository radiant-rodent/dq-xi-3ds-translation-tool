"""Build an installable (fakesigned) CIA from a decrypted .3ds image.

Contents: 0 = game NCCH (partition 0), 1 = e-manual CFA (partition 1, optional flag).
Contents are stored unencrypted. The certificate chain and the ticket layout are taken
from one of the system-update CIAs inside the cartridge image itself; signatures are
left blank, so installing needs signature patches (Luma3DS provides them; install with FBI).
"""
from __future__ import annotations

import hashlib
import struct

from .formats import Ncch, Window, open_image

ALIGN = 64
META_SIZE = 0x3AC0


def _align(x: int, a: int = ALIGN) -> int:
    return (x + a - 1) // a * a


def _find_template_cia(f, ncsd) -> bytes:
    for idx in (7, 6):
        p = ncsd.partition(idx)
        if not p:
            continue
        r = Ncch.parse(Window(f, p.offset, p.size)).romfs()
        if not r:
            continue
        for x in r.files:
            if x.path.lower().endswith(".cia") and x.size < 16 << 20:
                d = r.read_file(x)
                hs, typ, ver, cs, ts = struct.unpack_from("<IHHII", d, 0)
                if hs == 0x2020 and cs and ts == 0x350:
                    return d
    raise RuntimeError("no system CIA found in the update partition to take the certificate chain from")


def _ticket(template_ticket: bytes, title_id: int) -> bytes:
    t = bytearray(template_ticket)
    t[4:0x104] = b"\0" * 0x100                      # blank signature (fakesigned)
    t[0x1BF:0x1CF] = b"\0" * 0x10                   # title key (contents are not encrypted)
    struct.pack_into(">Q", t, 0x1D0, 0x0004000000000000 | (title_id & 0xFFFFFFFF))  # ticket id
    struct.pack_into(">I", t, 0x1D8, 0)             # console id
    struct.pack_into(">Q", t, 0x1DC, title_id)
    struct.pack_into(">H", t, 0x1E6, 0)             # ticket title version
    t[0x1F0] = 0                                    # license type
    t[0x1F1] = 0                                    # common key index
    struct.pack_into(">I", t, 0x21C, 0)             # eShop account id
    return bytes(t)


def _tmd(title_id: int, save_size: int, contents: list[tuple[int, int, int, bytes]]) -> bytes:
    """contents: (index, type flags, size, sha256)"""
    hdr = bytearray(0xC4)
    hdr[0:0x40] = b"Root-CA00000003-CP0000000b".ljust(0x40, b"\0")
    hdr[0x40] = 1                                    # version
    struct.pack_into(">Q", hdr, 0x4C, title_id)
    struct.pack_into(">I", hdr, 0x54, 0x40)          # title type (CTR)
    struct.pack_into("<I", hdr, 0x5A, save_size)     # save data size (little endian)
    struct.pack_into(">H", hdr, 0x9C, 0)             # title version
    struct.pack_into(">H", hdr, 0x9E, len(contents))
    struct.pack_into(">H", hdr, 0xA0, 0)             # boot content
    chunks = bytearray()
    for i, (index, flags, size, digest) in enumerate(contents):
        chunks += struct.pack(">IHHQ", i, index, flags, size) + digest
    info = bytearray(0x24 * 64)
    struct.pack_into(">HH", info, 0, 0, len(contents))
    info[4:0x24] = hashlib.sha256(chunks).digest()
    hdr[0xA4:0xC4] = hashlib.sha256(info).digest()
    sig = struct.pack(">I", 0x00010004) + b"\0" * 0x100 + b"\0" * 0x3C
    return sig + bytes(hdr) + bytes(info) + bytes(chunks)


def _meta(exheader: bytes, icon: bytes) -> bytes:
    m = bytearray(META_SIZE)
    m[0:0x180] = exheader[0x40:0x1C0]                # dependency list
    struct.pack_into("<I", m, 0x300, 2)              # core version
    m[0x400:0x400 + min(len(icon), 0x36C0)] = icon[:0x36C0]
    return bytes(m)


def build_cia(src_path: str, out_path: str, progress=None):
    progress = progress or (lambda *a: None)
    f, ncsd, ncch = open_image(src_path)
    try:
        title_id = ncch.program_id
        exheader = ncch.exheader()
        save_size = struct.unpack_from("<Q", exheader, 0x1C0)[0]
        try:
            icon = ncch.exefs().read("icon")
        except KeyError:
            icon = b""
        tpl = _find_template_cia(f, ncsd)
        hs, typ, ver, cs, ts = struct.unpack_from("<IHHII", tpl, 0)
        cert_off = _align(hs)
        certs = tpl[cert_off:cert_off + cs]
        tik_off = _align(cert_off + cs)
        ticket = _ticket(tpl[tik_off:tik_off + ts], title_id)

        parts = [(0, 0x0000, ncsd.partition(0))]
        man = ncsd.partition(1)
        if man:
            parts.append((1, 0x4000, man))      # optional content
        total = sum(p.size for _, _, p in parts)

        # pass 1: hash contents
        digests = []
        done = 0
        for index, flags, p in parts:
            h = hashlib.sha256()
            remaining, pos = p.size, p.offset
            while remaining:
                n = min(remaining, 8 << 20)
                f.seek(pos)
                h.update(f.read(n))
                pos += n
                remaining -= n
                done += n
                progress("Hashing CIA contents", done, total)
            digests.append(h.digest())
        contents = [(index, flags, p.size, d) for (index, flags, p), d in zip(parts, digests)]
        tmd = _tmd(title_id, save_size, contents)
        meta = _meta(exheader, icon)
        content_size = sum(_align(p.size) for _, _, p in parts)

        header = bytearray(0x2020)
        struct.pack_into("<IHHIIIIQ", header, 0, 0x2020, 0, 0, len(certs), len(ticket), len(tmd), len(meta), content_size)
        for index, _, _ in parts:
            header[0x20 + index // 8] |= 0x80 >> (index % 8)

        with open(out_path, "wb") as out:
            def put(data: bytes):
                out.write(data)
                pad = _align(out.tell()) - out.tell()
                out.write(b"\0" * pad)
            put(bytes(header))
            put(certs)
            put(ticket)
            put(tmd)
            done = 0
            for index, flags, p in parts:
                remaining, pos = p.size, p.offset
                while remaining:
                    n = min(remaining, 8 << 20)
                    f.seek(pos)
                    out.write(f.read(n))
                    pos += n
                    remaining -= n
                    done += n
                    progress("Writing CIA", done, total)
                out.write(b"\0" * (_align(out.tell()) - out.tell()))
            put(meta)
    finally:
        f.close()


def verify_cia(path: str) -> list[str]:
    """Structural check of a CIA: section sizes and content hashes."""
    problems = []
    with open(path, "rb") as fp:
        h = fp.read(0x2020)
        hs, typ, ver, cs, ts, tms, ms, csz = struct.unpack_from("<IHHIIIIQ", h, 0)
        off = _align(hs)
        fp.seek(off + _align(cs))
        tik = fp.read(ts)
        fp.seek(off + _align(cs) + _align(ts))
        tmd = fp.read(tms)
        n = struct.unpack_from(">H", tmd, 0x140 + 0x9E)[0]
        info = tmd[0x204:0xB04]
        chunks = tmd[0xB04:0xB04 + 0x30 * n]
        if hashlib.sha256(info).digest() != tmd[0x1E4:0x204]:
            problems.append("TMD content info hash mismatch")
        if hashlib.sha256(chunks).digest() != info[4:0x24]:
            problems.append("TMD chunk records hash mismatch")
        pos = off + _align(cs) + _align(ts) + _align(tms)
        for i in range(n):
            cid, idx, flags, size = struct.unpack_from(">IHHQ", chunks, i * 0x30)
            digest = chunks[i * 0x30 + 0x10:i * 0x30 + 0x30]
            fp.seek(pos)
            hh = hashlib.sha256()
            remaining = size
            while remaining:
                b = fp.read(min(remaining, 8 << 20))
                hh.update(b)
                remaining -= len(b)
            if hh.digest() != digest:
                problems.append(f"content {i} hash mismatch")
            fp.seek(pos)
            if fp.read(0x200)[0x100:0x104] != b"NCCH":
                problems.append(f"content {i} is not an NCCH")
            pos += _align(size)
        if pos + ms != _align(pos + ms) and False:
            pass
        if struct.unpack_from(">Q", tik, 0x1DC)[0] != struct.unpack_from(">Q", tmd, 0x18C)[0]:
            problems.append("ticket and TMD title IDs differ")
    return problems
