"""BFLIM (3DS layout image) decoding / encoding for the uncompressed pixel formats.

FLIM layout: pixel data, then a 0x28-byte footer: "FLIM" header (0x14) and "imag" block:
  0x1C u16 width, u16 height, u16 alignment, u8 format, u8 flags
Pixels are stored in 8x8 tiles, Morton order inside a tile. Flag 4 = the image is stored
rotated by 90 degrees (stored width/height are swapped).
"""
from __future__ import annotations

import struct

import numpy as np

FORMATS = {   # format -> (name, bits per pixel)
    0: ("L8", 8), 1: ("A8", 8), 2: ("LA4", 8), 3: ("LA8", 16), 5: ("RGB565", 16), 6: ("RGB8", 24),
    7: ("RGBA5551", 16), 8: ("RGBA4", 16), 9: ("RGBA8", 32), 12: ("L4", 4), 13: ("A4", 4),
}


def _morton(i: int) -> tuple[int, int]:
    x = y = 0
    for b in range(3):
        x |= ((i >> (2 * b)) & 1) << b
        y |= ((i >> (2 * b + 1)) & 1) << b
    return x, y


def _order(w: int, h: int) -> np.ndarray:
    """Linear pixel index -> stored (tiled) pixel index, as an h x w array."""
    o = np.zeros((h, w), dtype=np.int64)
    tw = w // 8
    for k in range(64):
        dx, dy = _morton(k)
        for ty in range(h // 8):
            o[ty * 8 + dy, dx::8][:tw] = (ty * tw + np.arange(tw)) * 64 + k
    return o


def info(data: bytes) -> dict:
    f = data[-0x28:]
    if f[:4] != b"FLIM":
        raise ValueError("not a BFLIM")
    w, h, align, fmt, flags = struct.unpack_from("<HHHBB", f, 0x1C)
    return {"width": w, "height": h, "format": fmt, "flags": flags, "size": len(data) - 0x28}


def _stored_dims(i):
    rot = bool(i["flags"] & 4)
    w, h = (i["height"], i["width"]) if rot else (i["width"], i["height"])
    # sizes are padded to powers of two (at least 8)
    p2 = lambda n: max(8, 1 << (n - 1).bit_length())
    return p2(w), p2(h), rot


_ETC_MOD = ((2, 8, -2, -8), (5, 17, -5, -17), (9, 29, -9, -29), (13, 42, -13, -42),
            (18, 60, -18, -60), (24, 80, -24, -80), (33, 106, -33, -106), (47, 183, -47, -183))


def _etc1_block(word: int) -> np.ndarray:
    """One ETC1 block (64-bit value) -> 4x4x3 uint8, indexed [y, x]."""
    diff, flip = word >> 33 & 1, word >> 32 & 1
    if diff:
        def ch(sh):
            c = word >> sh & 31
            d = word >> (sh - 3) & 7
            d = d - 8 if d & 4 else d
            c2 = c + d
            return (c << 3 | c >> 2), (c2 << 3 | c2 >> 2) & 255
        (r1, r2), (g1, g2), (b1, b2) = ch(59), ch(51), ch(43)
    else:
        r1, r2 = (word >> 60 & 15) * 17, (word >> 56 & 15) * 17
        g1, g2 = (word >> 52 & 15) * 17, (word >> 48 & 15) * 17
        b1, b2 = (word >> 44 & 15) * 17, (word >> 40 & 15) * 17
    t1, t2 = _ETC_MOD[word >> 37 & 7], _ETC_MOD[word >> 34 & 7]
    out = np.zeros((4, 4, 3), np.uint8)
    for x in range(4):
        for y in range(4):
            k = x * 4 + y
            idx = (word >> (16 + k) & 1) << 1 | (word >> k & 1)
            second = (y >= 2) if flip else (x >= 2)
            base, t = ((r2, g2, b2), t2) if second else ((r1, g1, b1), t1)
            out[y, x] = [min(255, max(0, c + t[idx])) for c in base]
    return out


def _decode_etc1(data: bytes, sw: int, sh: int, alpha: bool) -> np.ndarray:
    """3DS ETC1/ETC1A4: 8x8 tiles in rows, each 4 blocks (0,0) (4,0) (0,4) (4,4); a block is a
    little-endian 64-bit ETC1 word, preceded by 64 bits of 4-bit alpha (pixel x*4+y) in A4."""
    rgba = np.zeros((sh, sw, 4), np.uint8)
    step = 16 if alpha else 8
    pos = 0
    for ty in range(0, sh, 8):
        for tx in range(0, sw, 8):
            for bx, by in ((0, 0), (4, 0), (0, 4), (4, 4)):
                a = int.from_bytes(data[pos:pos + 8], "little") if alpha else None
                word = int.from_bytes(data[pos + step - 8:pos + step], "little")
                pos += step
                y0, x0 = ty + by, tx + bx
                rgba[y0:y0 + 4, x0:x0 + 4, :3] = _etc1_block(word)
                if alpha:
                    for x in range(4):
                        for y in range(4):
                            rgba[y0 + y, x0 + x, 3] = (a >> ((x * 4 + y) * 4) & 15) * 17
                else:
                    rgba[y0:y0 + 4, x0:x0 + 4, 3] = 255
    return rgba


def decode(data: bytes) -> np.ndarray:
    """RGBA uint8 array (height x width x 4), upright."""
    i = info(data)
    if i["format"] in (10, 11):                  # ETC1, ETC1A4
        sw, sh, rot = _stored_dims(i)
        rgba = _decode_etc1(data, sw, sh, i["format"] == 11)
        if rot:
            rgba = np.rot90(rgba, 1)
        return np.ascontiguousarray(rgba[:i["height"], :i["width"]])
    if i["format"] not in FORMATS:
        raise ValueError(f"unsupported BFLIM format {i['format']}")
    sw, sh, rot = _stored_dims(i)
    name, bpp = FORMATS[i["format"]]
    n = sw * sh
    raw = np.frombuffer(data[:n * bpp // 8], dtype=np.uint8)
    if bpp == 4:
        v = np.empty(n, np.uint8); v[0::2] = raw & 15; v[1::2] = raw >> 4; v = v * 17
        px = v
    elif bpp == 8:
        px = raw
    elif bpp == 16:
        px = raw.view("<u2")
    elif bpp == 24:
        px = raw.reshape(-1, 3)
    else:
        px = raw.reshape(-1, 4)
    lin = px[_order(sw, sh)]                     # sh x sw (x channels)
    rgba = np.zeros((sh, sw, 4), np.uint8)
    if name == "RGBA4":
        rgba[..., 0] = (lin >> 12 & 15) * 17; rgba[..., 1] = (lin >> 8 & 15) * 17
        rgba[..., 2] = (lin >> 4 & 15) * 17; rgba[..., 3] = (lin & 15) * 17
    elif name == "RGBA5551":
        rgba[..., 0] = (lin >> 11 & 31) * 255 // 31; rgba[..., 1] = (lin >> 6 & 31) * 255 // 31
        rgba[..., 2] = (lin >> 1 & 31) * 255 // 31; rgba[..., 3] = (lin & 1) * 255
    elif name == "RGB565":
        rgba[..., 0] = (lin >> 11 & 31) * 255 // 31; rgba[..., 1] = (lin >> 5 & 63) * 255 // 63
        rgba[..., 2] = (lin & 31) * 255 // 31; rgba[..., 3] = 255
    elif name == "RGBA8":
        rgba[...] = lin[..., ::-1]               # stored ABGR
    elif name == "RGB8":
        rgba[..., :3] = lin[..., ::-1]; rgba[..., 3] = 255
    elif name == "LA8":
        rgba[..., :3] = (lin >> 8)[..., None]; rgba[..., 3] = lin & 255
    elif name == "LA4":
        rgba[..., :3] = ((lin >> 4) * 17)[..., None]; rgba[..., 3] = (lin & 15) * 17
    elif name in ("L8", "L4"):
        rgba[..., :3] = lin[..., None]; rgba[..., 3] = 255
    elif name in ("A8", "A4"):
        rgba[..., :3] = 255; rgba[..., 3] = lin
    if rot:
        rgba = np.rot90(rgba, 1)
    return np.ascontiguousarray(rgba[:i["height"], :i["width"]])


def encode(rgba: np.ndarray, original: bytes) -> bytes:
    """Pixels (same size as the original image) in the original's format and layout."""
    i = info(original)
    sw, sh, rot = _stored_dims(i)
    name, bpp = FORMATS[i["format"]]
    full = np.zeros((sh, sw, 4), np.uint8) if not rot else np.zeros((sw, sh, 4), np.uint8)
    full[:rgba.shape[0], :rgba.shape[1]] = rgba
    if rot:
        full = np.rot90(full, -1)
    r, g, b, a = (full[..., k].astype(np.uint32) for k in range(4))
    if name == "RGBA4":
        lin = (r * 15 + 127) // 255 << 12 | (g * 15 + 127) // 255 << 8 | (b * 15 + 127) // 255 << 4 | (a * 15 + 127) // 255
        dt = "<u2"
    elif name == "RGBA5551":
        lin = (r * 31 + 127) // 255 << 11 | (g * 31 + 127) // 255 << 6 | (b * 31 + 127) // 255 << 1 | (a >= 128)
        dt = "<u2"
    elif name == "RGB565":
        lin = (r * 31 + 127) // 255 << 11 | (g * 63 + 127) // 255 << 5 | (b * 31 + 127) // 255
        dt = "<u2"
    elif name == "RGBA8":
        lin = np.stack([a, b, g, r], -1); dt = np.uint8
    elif name == "LA8":
        lin = r << 8 | a; dt = "<u2"
    elif name == "LA4":
        lin = (r * 15 + 127) // 255 << 4 | (a * 15 + 127) // 255; dt = np.uint8
    elif name in ("A8",):
        lin = a; dt = np.uint8
    else:
        raise ValueError(f"encoding {name} is not supported")
    stored = np.zeros((sh * sw,) + lin.shape[2:], dtype=lin.dtype)
    stored[_order(sw, sh)] = lin
    body = stored.astype(dt).tobytes()
    return body + original[len(body):]


def encode_rgba4(rgba: np.ndarray, original: bytes) -> bytes:
    """Pixels as a new RGBA4 BFLIM of the original's size: for images in a format there is no
    encoder for (ETC1). Stored upright (rotation flags cleared); the layouts name the image
    only, so the format may change (twice the memory of ETC1A4)."""
    i = info(original)
    foot = bytearray(original[-0x28:])
    foot[0x14 + 0x0F] = foot[0x14 + 0x0F] & ~0x0C & 0xFF       # flags: no rotation
    foot[0x14 + 0x0E] = 8                                       # RGBA4
    struct.pack_into("<I", foot, 0x24, 0)
    tmp = bytes(_stored_bytes(i["width"], i["height"], 16)) + bytes(foot)
    body = encode(rgba, tmp)[:-0x28]
    struct.pack_into("<I", foot, 0x24, len(body))
    struct.pack_into("<I", foot, 0x0C, len(body) + 0x28)
    return body + bytes(foot)


def _stored_bytes(w: int, h: int, bpp: int) -> int:
    p2 = lambda n: max(8, 1 << (n - 1).bit_length())      # noqa: E731
    return p2(w) * p2(h) * bpp // 8
