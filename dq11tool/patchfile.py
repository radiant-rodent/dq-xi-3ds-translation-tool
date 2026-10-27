"""Distributable translation patch (.dq11patch) for the browser patcher (docs/).

A patch holds, for every RomFS / ExeFS file the export changes, a delta against the same file
of the player's own Japanese ROM, so it carries no original game data. The browser rebuilds
the .3ds the same way ctr/build.py does (docs/dq11patch.js).

File layout: gzip of
  "DQ11PTCH" u32 header_len  header (UTF-8 JSON)  delta blobs
header: {"format": 1, "name", "version", "source": {...}, "keep_update_partitions",
         "files": [entry, ...], "cia": {...} (optional)}
entry:  {"path": RomFS path or "exefs:<name>", "src_size", "src_sha256", "src_dec",
         "dst_size", "dst_sha256", "dst_dec_size", "dst_enc", "off", "len"}
  src_dec  "raw" | "blz"          the original file as the delta source (BLZ-decompressed)
  dst_enc  "raw" | "blz" | "paca" the delta output is encoded with blz.compress (whole, or
                                  after the first 8 bytes = PACA header, see pack.py)
  off/len  delta blob in the data area: varint ops until dst_dec_size bytes are produced
             v = varint; v & 1 == 0: copy v >> 1 bytes from the source at
                 prev_end + zigzag(varint) (prev_end = end of the previous copy)
             v & 1 == 1: insert v >> 1 literal bytes that follow
source: product code, title id and the SHA-256 of the NCCH header without its flags (which
        decryption tools rewrite); the header carries the ExeFS and RomFS master hashes.
cia:    {"ncch_sha256", "size", "sha256"} of the patched game partition: the SHA-256 of its NCCH
        header, its size and the SHA-256 of the whole partition (the .cia content hash). When the
        browser built the same NCCH header (it covers the ExeFS and RomFS through their master
        hashes), it takes the content hash from here instead of reading the whole game again.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import struct

from . import blz
from .ctr.formats import open_image

MAGIC = b"DQ11PTCH"
FORMAT = 1
BLOCK, STRIDE = 16, 4   # delta matcher: 16-byte keys indexed every 4 bytes of the source


def source_id(ncch_header: bytes) -> str:
    return hashlib.sha256(ncch_header[:0x188] + ncch_header[0x190:0x200]).hexdigest()


def _varint(out: bytearray, v: int):
    while v >= 0x80:
        out.append(v & 0x7F | 0x80)
        v >>= 7
    out.append(v)


def delta(src: bytes, dst: bytes) -> bytes:
    """Copy/insert ops that turn src into dst (greedy matcher, see module docstring)."""
    idx: dict[bytes, int] = {}
    for p in range(0, len(src) - BLOCK + 1, STRIDE):
        idx.setdefault(src[p:p + BLOCK], p)
    out = bytearray()
    lit = bytearray()
    prev_end = 0
    i, n, ns = 0, len(dst), len(src)
    get = idx.get

    def flush():
        if lit:
            _varint(out, len(lit) << 1 | 1)
            out.extend(lit)
            lit.clear()

    while i < n:
        p = get(dst[i:i + BLOCK]) if i + BLOCK <= n else None
        if p is None:
            lit.append(dst[i])
            i += 1
            continue
        back = 0
        while back < len(lit) and back < p and src[p - back - 1] == dst[i - back - 1]:
            back += 1
        ln = BLOCK
        while i + ln < n and p + ln < ns and src[p + ln] == dst[i + ln]:
            ln += 1
        if back:
            del lit[-back:]
        flush()
        start = p - back
        rel = start - prev_end
        _varint(out, (ln + back) << 1)
        _varint(out, rel << 1 if rel >= 0 else (-rel << 1) - 1)
        prev_end = start + ln + back
        i += ln
    flush()
    return bytes(out)


def apply_delta(src: bytes, ops: bytes, size: int) -> bytes:
    """Inverse of delta() (self-check; the browser has its own copy)."""
    out = bytearray()
    pos = prev_end = 0

    def varint():
        nonlocal pos
        v = shift = 0
        while True:
            b = ops[pos]
            pos += 1
            v |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                return v

    while len(out) < size:
        v = varint()
        if v & 1:
            out += ops[pos:pos + (v >> 1)]
            pos += v >> 1
        else:
            z = varint()
            start = prev_end + (-(z + 1 >> 1) if z & 1 else z >> 1)
            out += src[start:start + (v >> 1)]
            prev_end = start + (v >> 1)
    return bytes(out)


def _blz(path: str, data: bytes) -> bool:
    """BLZ-compressed files: PACA archives and the ExeFS code."""
    return data[:4] == b"PACA" or path == "exefs:.code"


def _decode(path: str, data: bytes) -> tuple[str, bytes]:
    """The form an original file is diffed in: BLZ-compressed files decompressed."""
    if _blz(path, data):
        try:
            return "blz", blz.decompress(data)
        except ValueError:
            pass
    return "raw", data


def _encode_target(path: str, data: bytes) -> tuple[str, bytes]:
    """(encoding, decoded bytes) such that the browser's re-encoding gives `data` exactly."""
    if _blz(path, data):
        try:
            dec = blz.decompress(data)
        except ValueError:
            return "raw", data
        if data[:4] == b"PACA":
            if data[:8] + (blz.compress(dec[8:]) or b"") == data:
                return "paca", dec
        elif blz.compress(dec) == data:
            return "blz", dec
    return "raw", data


def write_patch(rom_path: str, out_path: str, romfs_repl: dict[str, bytes],
                exefs_repl: dict[str, bytes], name: str = "", version: str = "",
                keep_update_partitions: bool = True, progress=None, cia: dict | None = None) -> dict:
    progress = progress or (lambda *a: None)
    f, ncsd, ncch = open_image(rom_path)
    try:
        romfs, exefs = ncch.romfs(), ncch.exefs()
        by_path = {rf.path: rf for rf in romfs.files}
        items = [(p, romfs.read_file(by_path[p]), d) for p, d in sorted(romfs_repl.items())]
        items += [("exefs:" + k, exefs.read(k), d) for k, d in sorted(exefs_repl.items())]
        header = {"format": FORMAT, "name": name, "version": version,
                  "source": {"product_code": ncch.product_code,
                             "title_id": f"{ncch.program_id:016X}",
                             "ncch_sha256": source_id(ncch.header)},
                  "keep_update_partitions": keep_update_partitions, "files": []}
        blobs = bytearray()
        for i, (path, orig, new) in enumerate(items):
            if i % 32 == 0:
                progress("Writing patch", i, len(items))
            src_dec, src = _decode(path, orig)
            dst_enc, dst = _encode_target(path, new)
            ops = delta(src, dst)
            assert apply_delta(src, ops, len(dst)) == dst, path
            header["files"].append({
                "path": path, "src_size": len(orig), "src_sha256": hashlib.sha256(orig).hexdigest(),
                "src_dec": src_dec, "dst_size": len(new), "dst_sha256": hashlib.sha256(new).hexdigest(),
                "dst_dec_size": len(dst), "dst_enc": dst_enc, "off": len(blobs), "len": len(ops)})
            blobs += ops
        progress("Writing patch", len(items), len(items))
    finally:
        f.close()
    if not version:   # same translation, same version (and the same patch file)
        version = hashlib.sha256("".join(e["dst_sha256"] for e in header["files"]).encode()).hexdigest()[:12]
        header["version"] = version
    if cia:
        header["cia"] = cia
    hj = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(out_path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as fp:
        fp.write(MAGIC + struct.pack("<I", len(hj)) + hj)
        fp.write(blobs)
    return header


def write_layeredfs_zip(out_path: str, title_id: str, romfs_repl: dict[str, bytes],
                        code: bytes | None) -> str:
    """Luma3DS game patching folder (luma/titles/<title id>/romfs + decompressed code.bin) as a
    zip; deterministic (sorted names, fixed timestamps), so it only changes with the content.
    Returns its SHA-256."""
    import zipfile
    entries = [(f"luma/titles/{title_id}/romfs{p}", d) for p, d in sorted(romfs_repl.items())]
    if code is not None:
        entries.append((f"luma/titles/{title_id}/code.bin", code))
    with zipfile.ZipFile(out_path, "w") as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data, compresslevel=9)
    with open(out_path, "rb") as fp:
        return hashlib.sha256(fp.read()).hexdigest()


def cia_content(image_path: str) -> dict:
    """The "cia" header entry for a built .3ds (see the module docstring)."""
    f, ncsd, _ncch = open_image(image_path)
    try:
        p0 = ncsd.partition(0)
        f.seek(p0.offset)
        head = f.read(0x200)
        h = hashlib.sha256(head)
        remaining = p0.size - len(head)
        while remaining:
            chunk = f.read(min(remaining, 8 << 20))
            h.update(chunk)
            remaining -= len(chunk)
        return {"ncch_sha256": hashlib.sha256(head).hexdigest(), "size": p0.size, "sha256": h.hexdigest()}
    finally:
        f.close()


SITE_PATCH = "DQXI_EN.dq11patch"
SITE_ZIP = "DQXI_EN_layeredfs.zip"


def main(argv=None):
    """python -m dq11tool.patchfile WORKSPACE [--site DIR] [--patch P] [--layeredfs Z] [--3ds I]
    Exports the project with the default options and writes the browser patch and/or the
    LayeredFS zip (--site DIR: both, under the names the web patcher in docs/ links to), and
    optionally the reference .3ds that the browser patcher must reproduce byte for byte."""
    import argparse
    import os

    from .ctr.build import build_image
    from .export import ExportOptions, collect_changes
    from .project import Project

    ap = argparse.ArgumentParser(prog="python -m dq11tool.patchfile")
    ap.add_argument("workspace")
    ap.add_argument("--site", help="write DQXI_EN.dq11patch and DQXI_EN_layeredfs.zip here (docs/patch)")
    ap.add_argument("--patch", help="browser patch (.dq11patch)")
    ap.add_argument("--layeredfs", help="Luma3DS LayeredFS zip")
    ap.add_argument("--3ds", dest="image", help="reference .3ds (tests/patcher_test.mjs)")
    ap.add_argument("--version", default="", help="default: a hash of the patched files")
    a = ap.parse_args(argv)
    if a.site:
        a.patch = a.patch or os.path.join(a.site, SITE_PATCH)
        a.layeredfs = a.layeredfs or os.path.join(a.site, SITE_ZIP)
    if not (a.patch or a.layeredfs or a.image):
        ap.error("nothing to write: give --site, --patch, --layeredfs or --3ds")

    def prog(stage, i, n):
        print(f"\r{stage} {i}/{n}   ", end="", flush=True)

    project = Project(a.workspace)
    opts = ExportOptions(make_3ds=False, make_layeredfs=False)
    romfs_repl, exefs_repl, _report, code = collect_changes(project, opts, prog)
    print()
    if a.image:
        build_image(project.rom_path, a.image, romfs_repl, exefs_repl, prog,
                    keep_update_partitions=opts.keep_update_partitions)
        print()
    if a.patch:
        # the .cia content hash needs the built game: cached, keyed by everything that goes into it
        key = hashlib.sha256()
        with open(os.path.join(os.path.dirname(__file__), "ctr", "build.py"), "rb") as fp:
            key.update(fp.read())
        with open(project.rom_path, "rb") as fp:
            key.update(fp.read(0x10000))
        for name, data in sorted(romfs_repl.items()) + sorted(("exefs:" + k, v) for k, v in exefs_repl.items()):
            key.update(name.encode() + hashlib.sha256(data).digest())
        key.update(b"keep" if opts.keep_update_partitions else b"drop")
        cache = os.path.join(a.workspace, "cache", "patch_cia.json")
        try:
            with open(cache, encoding="utf-8") as fp:
                cached = json.load(fp)
        except (OSError, ValueError):
            cached = {}
        cia = cached.get("cia") if cached.get("key") == key.hexdigest() else None
        if a.image:
            cia = cia_content(a.image)
        elif not cia:
            tmp = os.path.join(a.workspace, "cache", "patch_cia.3ds")
            build_image(project.rom_path, tmp, romfs_repl, exefs_repl, prog,
                        keep_update_partitions=opts.keep_update_partitions)
            print()
            try:
                cia = cia_content(tmp)
            finally:
                os.remove(tmp)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fp:
            json.dump({"key": key.hexdigest(), "cia": cia}, fp)
        h = write_patch(project.rom_path, a.patch, romfs_repl, exefs_repl,
                        "Dragon Quest XI (3DS) English", a.version, opts.keep_update_partitions, prog, cia)
        print(f"\n{a.patch}: version {h['version']}, {len(h['files'])} files")
    if a.layeredfs:
        tid = project.config.get("title_id", "0004000000199200")
        digest = write_layeredfs_zip(a.layeredfs, tid, romfs_repl, code)
        print(f"{a.layeredfs}: sha256 {digest}")


if __name__ == "__main__":
    main()
