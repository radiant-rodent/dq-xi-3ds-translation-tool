"""The title screen logo with the official English subtitle ("Echoes of an Elusive Age").

The logo textures (logo_3d.pack title_logo_3d.bflim, 512x256 RGBA8, and logo_2d.pack
title_logo_2d.bflim, 512x256 RGB565 over a dark background) carry the Japanese subtitle
"ドラゴンクエストXI 過ぎ去りし時を求めて" under the emblem. The official English logo artwork
(LOGO_FILE, supplied by the user; a PNG with transparency, or on white, which is keyed out)
replaces the whole logo: it is fitted into the bounding box of the original logo's pixels and
centred on it, so the layout's position and animation stay as they were.
"""
from __future__ import annotations

import os

import numpy as np

LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logo_en.png")
TEXTURES = {"timg/title_logo_3d.bflim": "alpha", "timg/title_logo_2d.bflim": "opaque"}
PACKS = {"logo_3d.pack", "logo_2d.pack"}


def available() -> bool:
    return os.path.isfile(LOGO_FILE)


def _source() -> np.ndarray:
    """The English logo as straight RGBA floats 0..1; a white background becomes transparent
    (colour-to-alpha against white, as image editors do)."""
    from PIL import Image
    im = Image.open(LOGO_FILE).convert("RGBA")
    a = np.asarray(im).astype(np.float32) / 255.0
    if a[..., 3].min() < 0.99:                    # has its own transparency
        return a
    rgb = a[..., :3]
    alpha = np.clip((1.0 - rgb.min(axis=2)) / 1.0, 0, 1)
    alpha = np.clip(alpha * 1.15, 0, 1)            # keep the faint smoke visible
    out = np.zeros_like(a)
    safe = np.maximum(alpha, 1e-4)[..., None]
    out[..., :3] = np.clip((rgb - (1.0 - alpha[..., None])) / safe, 0, 1)
    out[..., 3] = alpha
    return out


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _fit(src: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, int, int]:
    """src scaled to fit into box (keeping its aspect), and its top-left corner."""
    from PIL import Image
    sb = _bbox(src[..., 3] > 0.02)
    src = src[sb[1]:sb[3], sb[0]:sb[2]]
    bw, bh = box[2] - box[0], box[3] - box[1]
    k = min(bw / src.shape[1], bh / src.shape[0])
    w, h = max(1, round(src.shape[1] * k)), max(1, round(src.shape[0] * k))
    # premultiplied resize (no dark fringes)
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4]
    chans = [np.asarray(Image.fromarray((pm[..., c] * 65535).astype(np.uint16).astype(np.int32), "I")
                        .resize((w, h), Image.LANCZOS)).astype(np.float32) / 65535 for c in range(4)]
    out = np.clip(np.stack(chans, -1), 0, 1)
    rgb = out[..., :3] / np.maximum(out[..., 3:4], 1e-4)
    out[..., :3] = np.clip(rgb, 0, 1)
    x0 = box[0] + (bw - w) // 2
    y0 = box[1] + (bh - h) // 2
    return out, x0, y0


def redraw(bflim: bytes, mode: str) -> bytes:
    from . import texture
    px = texture.decode(bflim).astype(np.float32) / 255.0
    src = _source()
    if mode == "alpha":
        box = _bbox(px[..., 3] > 0.05)
        new, x0, y0 = _fit(src, box)
        out = np.zeros_like(px)
        out[y0:y0 + new.shape[0], x0:x0 + new.shape[1]] = new
    else:
        bg = px[2, 2, :3].copy()                  # the dark background the logo sits on
        diff = np.abs(px[..., :3] - bg).max(axis=2) > 0.06
        # only the 400x240 the screen shows (the rest of the sheet is grey), without the
        # "2D版：仮" note in its bottom right corner
        diff[:, 400:] = False
        diff[240:, :] = False
        diff[195:, 280:] = False
        box = _bbox(diff)
        new, x0, y0 = _fit(src, box)
        out = px.copy()
        out[box[1]:box[3], box[0]:box[2], :3] = bg
        region = out[y0:y0 + new.shape[0], x0:x0 + new.shape[1], :3]
        a = new[..., 3:4]
        out[y0:y0 + new.shape[0], x0:x0 + new.shape[1], :3] = new[..., :3] * a + region * (1 - a)
        out[..., 3] = 1.0
    return texture.encode((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), bflim)


def patch_pack(pack_data: bytes) -> bytes | None:
    from .darc import Sarc
    from .pack import Pack
    if not available():
        return None
    pack = Pack.parse(pack_data)
    changed = False
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        repl = {}
        for n in sarc.nodes:
            if n.name in TEXTURES:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw(raw, TEXTURES[n.name])
        if repl:
            e.data = sarc.rebuild(repl)
            changed = True
    return pack.build() if changed else None
