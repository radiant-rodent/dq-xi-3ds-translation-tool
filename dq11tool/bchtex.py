"""Pictures in BCH files (ui/tutorial/*.bch: the tutorial screenshots), read and replaced in place.

Ported from the disassembler project (tools/bch_texture_patch.py, tools/etc1_encode.py): the
texture table is found through the BCH header, the format and data address through the PICA
command stream; a replacement is encoded to the same format and written over the base level
(same size, headers untouched).
"""
from __future__ import annotations

import struct

import numpy as np

PICA_FORMATS = {0: "RGBA8", 1: "RGB8", 2: "RGBA5551", 3: "RGB565", 4: "RGBA4", 5: "LA8", 6: "HiLo8",
                7: "L8", 8: "A8", 9: "LA4", 10: "L4", 11: "A4", 12: "ETC1", 13: "ETC1A4"}
_BPP = {"ETC1": 4, "ETC1A4": 8}


def textures(data: bytes) -> list[dict]:
    """The ETC1/ETC1A4 textures: name, width, height, stored size, format, file offset."""
    if data[:4] != b"BCH\0":
        raise ValueError("not a BCH file")
    hdr_off, str_off, cmd_off, data_off = struct.unpack_from("<4I", data, 8)
    str_len = struct.unpack_from("<I", data, 0x2C)[0]
    tree, count = struct.unpack_from("<2I", data, hdr_off + 3 * 12)
    out = []
    for ptr in struct.unpack_from(f"<{count}I", data, hdr_off + tree) if count else ():
        words = struct.unpack_from("<8I", data, hdr_off + ptr)
        name = ""
        if words[7] < str_len:
            s = str_off + words[7]
            name = data[s:data.index(b"\0", s)].decode("ascii", "replace")
        dim = struct.unpack_from("<I", data, cmd_off + words[0])[0]
        width, height = (dim >> 16) & 0xFFFF, dim & 0xFFFF       # height low, width high
        fmt, addr = words[6] & 0xFF, 0
        block = struct.unpack_from("<10I", data, cmd_off + words[0])
        for i, cw in enumerate(block):                  # value precedes its register word
            reg = cw & 0xFFFF
            if reg in (0x8E, 0x9E, 0xA6) and i:
                fmt = block[i - 1]
            elif reg in (0x85, 0x95, 0x9D) and i:
                addr = block[i - 1]
        name_fmt = PICA_FORMATS.get(fmt)
        if name_fmt not in _BPP:
            continue
        p2 = lambda n: max(8, 1 << (n - 1).bit_length())          # noqa: E731
        out.append({"name": name, "width": width, "height": height, "stored": (p2(width), p2(height)),
                    "format": name_fmt, "offset": data_off + addr})
    return out


def decode(data: bytes, tex: dict) -> np.ndarray:
    from .texture import _decode_etc1
    sw, sh = tex["stored"]
    size = sw * sh * _BPP[tex["format"]] // 8
    px = _decode_etc1(data[tex["offset"]:tex["offset"] + size], sw, sh, tex["format"] == "ETC1A4")
    return px[:tex["height"], :tex["width"]]


def replace(data: bytes, tex: dict, rgba: np.ndarray) -> bytes:
    """data with the texture's base level replaced by rgba (its width x height)."""
    if rgba.shape[:2] != (tex["height"], tex["width"]):
        raise ValueError(f"{tex['name']}: the image must be {tex['width']}x{tex['height']}")
    sw, sh = tex["stored"]
    full = np.zeros((sh, sw, 4), np.uint8)
    full[:tex["height"], :tex["width"]] = rgba
    enc = encode_etc1(full, tex["format"] == "ETC1A4")
    out = bytearray(data)
    out[tex["offset"]:tex["offset"] + len(enc)] = enc
    return bytes(out)


# ---------------------------------------------------------------------- ETC1 encoder
# Each half-block gets its quantised average colour and the best of the eight modifier tables
# (individual mode, both flip directions tried): plain, but enough for screenshots.

_ETC_MOD = ((2, 8, -2, -8), (5, 17, -5, -17), (9, 29, -9, -29), (13, 42, -13, -42),
            (18, 60, -18, -60), (24, 80, -24, -80), (33, 106, -33, -106), (47, 183, -47, -183))
_HALVES = {(flip, second): [(x, y) for x in range(4) for y in range(4)
                            if ((y >= 2) if flip else (x >= 2)) == bool(second)]
           for flip in (0, 1) for second in (0, 1)}


def _quant(v) -> np.ndarray:
    return np.clip(np.round(np.asarray(v) / 17.0), 0, 15).astype(int)


def _encode_block(px: np.ndarray, alpha: bool) -> bytes:
    best = None
    for flip in (0, 1):
        halves, err = [], 0.0
        for second in (0, 1):
            coords = _HALVES[flip, second]
            pix = np.array([px[y, x, :3] for x, y in coords], np.float32)          # 8 x 3
            base = _quant(pix.mean(0))
            cand = []
            for ti, table in enumerate(_ETC_MOD):
                vals = base[None, :] * 17 + np.array(table, np.float32)[:, None]   # 4 x 3
                d = ((pix[:, None, :] - vals[None]) ** 2).sum(-1)                  # 8 x 4
                cand.append((float(d.min(1).sum()), ti, d.argmin(1)))
            e, ti, idx = min(cand, key=lambda c: c[0])
            err += e
            halves.append((base, ti, idx))
        if best is None or err < best[0]:
            best = (err, flip, halves)
    _, flip, ((b1, t1, i1), (b2, t2, i2)) = best
    word = (flip & 1) << 32 | (t1 & 7) << 37 | (t2 & 7) << 34
    word |= int(b1[0]) << 60 | int(b2[0]) << 56 | int(b1[1]) << 52 | int(b2[1]) << 48
    word |= int(b1[2]) << 44 | int(b2[2]) << 40
    for second, idxs in ((0, i1), (1, i2)):
        for (x, y), idx in zip(_HALVES[flip, second], idxs):
            k = x * 4 + y
            word |= (int(idx) & 1) << k | (int(idx) >> 1 & 1) << (16 + k)
    colour = word.to_bytes(8, "little")
    if not alpha:
        return colour
    a = 0
    for x in range(4):
        for y in range(4):
            a |= int(_quant(px[y, x, 3])) << ((x * 4 + y) * 4)
    return a.to_bytes(8, "little") + colour


def encode_etc1(rgba: np.ndarray, alpha: bool = False) -> bytes:
    """HxWx4 (multiples of 8) -> ETC1/ETC1A4 in the 3DS tile order."""
    h, w = rgba.shape[:2]
    out = bytearray()
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for bx, by in ((0, 0), (4, 0), (0, 4), (4, 4)):
                out += _encode_block(rgba[ty + by:ty + by + 4, tx + bx:tx + bx + 4], alpha)
    return bytes(out)
