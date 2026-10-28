"""What Luma3DS writes into the game's code when it loads a LayeredFS mod (luma/titles/<id>/romfs).

A real 3DS that plays the translation through Luma's game patching (the LayeredFS zip) runs the
code.bin *after* Luma's loader patched it (sysmodules/loader/source/patcher.c, patchLayeredFs):

- its RomFS redirection payload (romfsredir.s, PAYLOAD_SIZE bytes) goes to the end of .text when
  the padding up to the next page holds it (here: 0x72403C), else over the function that calls
  svcConnectToPort for a fatal error;
- the mod's path ("lf:/luma/titles/<id>/romfs", PATH_SIZE bytes) goes to the end of .rodata
  (or .data) when that padding has 39 bytes;
- fsOpenFileDirectly and fsTryOpenFile get a branch to the payload in their first word.

Azahar's own LayeredFS does none of this, so emulator tests cannot see a collision: our code
caves used to start at 0x724040, Luma's payload overwrote the runtime fit code and a real 3DS
crashed right after the title screen (2026-10-28). `check` must find no collision before the
LayeredFS zip is published (patchfile.write_layeredfs_zip refuses it); `simulate` writes the
same areas so emutest/lumasim.py can boot such a code.bin in Azahar.
"""
from __future__ import annotations

import struct

BASE = 0x100000
PAYLOAD_SIZE = 0x114          # romfsredir.s assembled (Luma3DS master, 2026-10-28): 69 words
PAYLOAD_RESERVE = 0x200       # kept free for a bigger payload in later Luma versions
PATH_SIZE = 39                # "lf:" + "/luma/titles/0004000000199200/romfs" + NUL (Luma reserves 39)


def segments(exheader: bytes) -> list[tuple[int, int, int]]:
    """[(address, pages, size)] of .text, .rodata, .data (exheader code set info)."""
    return [struct.unpack_from("<III", exheader, 0x10 + i * 0x10) for i in range(3)]


def _func_start(code: bytes, pos: int) -> int | None:
    while pos >= 4:
        pos -= 4
        if code[pos + 2:pos + 4] == b"\x2d\xe9":          # push {...}
            return pos
    return None


def _words(code: bytes, size: int):
    return struct.unpack_from(f"<{size // 4}I", code, 0)


def hook_sites(code: bytes, exheader: bytes) -> dict[str, int]:
    """File offsets of the functions Luma's findLayeredFsSymbols finds (it patches only
    fsOpenFileDirectly and fsTryOpenFile; the others are called from the payload)."""
    size = segments(exheader)[0][2]
    w = _words(code, size)
    n = len(w)
    found: dict[str, int] = {}
    for i, v in enumerate(w):
        name = None
        if v == 0xE5970010 and i + 2 < n and w[i + 1] == 0xE1CD20D8 and (w[i + 2] & 0xFFFFFF) == 0x008D0000:
            name = "fsMountArchive"
        elif v == 0xE24DD028 and i + 3 < n and w[i + 1:i + 4] == (0xE1A04000, 0xE59F60A8, 0xE3A0C001):
            name = "fsMountArchive"
        elif v == 0xE2844001 and i + 2 < n and w[i + 1] == 0xE3540020 and w[i + 2] == 0x3AFFFFF0:
            name = "fsUnmountArchive"
        elif v == 0xE353003A and i + 2 < n and (w[i + 1] & 0xFFFFFF0F) == 0x0A000009 \
                and (w[i + 2] & 0xFFFF0FF0) == 0xE1A00400:
            name = "fsUnmountArchive"
        elif v == 0xE3500008 and i + 2 < n and (w[i + 1] & 0xFFF00FF0) == 0xE1800400 \
                and (w[i + 2] & 0xFFF00FF0) == 0xE1800FC0:
            name = "fsRegisterArchive"
        elif v == 0xE351003A and i + 0xF < n and w[i + 1] == 0x1AFFFFFC and w[i + 0xD] == 0xE590C000 \
                and w[i + 0xF] == 0xE12FFF3C:
            name = "fsTryOpenFile"
        elif v == 0x08030204:
            name = "fsOpenFileDirectly"
        if name and name not in found:
            f = _func_start(code, i * 4)
            if f is not None:
                found[name] = f
                if len(found) == 5:
                    break
    return found


def regions(exheader: bytes) -> list[tuple[int, int, str]]:
    """[(start, end, what)] code addresses Luma overwrites (payload incl. PAYLOAD_RESERVE, path).
    Raises if the game's padding is too small for the end-of-segment placement (Luma would then
    pick a game function instead, which this module does not model)."""
    (ta, _tp, ts), (ra, _rp, rs), (da, _dp, ds) = segments(exheader)
    rnd = lambda x: (x + 0xFFF) & ~0xFFF                   # noqa: E731
    if rnd(ts) - ts < PAYLOAD_SIZE:
        raise ValueError("Luma would place its LayeredFS payload over a game function (not modelled)")
    out = [(ta + ts, min(ta + rnd(ts), ta + ts + PAYLOAD_RESERVE), "Luma LayeredFS payload")]
    if rnd(rs) - rs >= PATH_SIZE:
        out.append((ra + rs, ra + rs + PATH_SIZE, "Luma LayeredFS path"))
    elif rnd(ds) - ds >= PATH_SIZE:
        out.append((da + ds, da + ds + PATH_SIZE, "Luma LayeredFS path"))
    else:
        raise ValueError("Luma would place its LayeredFS path over a game function (not modelled)")
    return out


def check(code: bytes, original: bytes, exheader: bytes) -> list[str]:
    """Problems if Luma's LayeredFS patching would overwrite anything the translation changed in
    code.bin (original: the game's decompressed code). Empty list = safe."""
    problems = []
    for start, end, what in regions(exheader):
        a, b = start - BASE, end - BASE
        diff = [i for i in range(a, b) if code[i] != original[i]]
        if diff:
            problems.append(f"{what} {start:#x}-{end:#x} overwrites changed code at "
                            f"{BASE + diff[0]:#x}..{BASE + diff[-1]:#x} ({len(diff)} bytes)")
    mine, theirs = hook_sites(code, exheader), hook_sites(original, exheader)
    if mine != theirs:
        problems.append(f"Luma finds other fs functions than in the original game: {mine} vs {theirs}")
    for name in ("fsOpenFileDirectly", "fsTryOpenFile"):
        if name not in theirs:
            problems.append(f"Luma finds no {name}: LayeredFS would not load")
            continue
        o = theirs[name]
        if code[o:o + 4] != original[o:o + 4]:
            problems.append(f"Luma hooks {name} at {BASE + o:#x}, which the translation changed")
    return problems


def simulate(code: bytes, exheader: bytes) -> bytes:
    """code.bin as Luma leaves it, for an emulator (whose own LayeredFS does the file
    redirection): the payload area (PAYLOAD_SIZE) filled with undefined instructions, so any
    use of it crashes at once, and the path string written. The fs hooks are not placed."""
    out = bytearray(code)
    (start, _e, _w), (pstart, _pe, _pw) = regions(exheader)
    a = start - BASE
    out[a:a + PAYLOAD_SIZE] = b"\xf0\x00\xf0\xe7" * (PAYLOAD_SIZE // 4)      # udf #0x0f
    p = pstart - BASE
    out[p:p + PATH_SIZE] = b"lf:/luma/titles/0004000000199200/romfs\0"[:PATH_SIZE]
    return bytes(out)


def check_rom(code: bytes, rom_path: str) -> list[str]:
    """check() against the decompressed code and exheader of the original game image."""
    from . import blz
    from .ctr.formats import open_image
    f, _ncsd, ncch = open_image(rom_path)
    try:
        exheader = ncch.exheader()
        original = ncch.exefs().read(".code")
    finally:
        f.close()
    if exheader[0x0D] & 1:
        original = blz.decompress(original)
    return check(code, original, exheader)
