"""Japanese text drawn into textures (labels without a text pane), redrawn in English.

The labels are a single line of text on a transparent background: a fill (gold, top to bottom
gradient on the town banners and map hints) and a dark outline around it. restyle() measures
both from the Japanese image - the fill colour of every row and the outline colour - clears the
image and draws the English with a game font (1:1, strokes widened by a pixel like the bold
Japanese), the fill in the same row colours, the outline around it. A text wider than the image
is narrowed to fit; it keeps the Japanese text's alignment (centred, or from its left edge).

Town banners (ui/location/location_*.bch, shown when entering a place): one 256x32 texture per
file, RGBA4 (16 KB) or ETC1A4 (8 KB). Every RGBA4 file has the same header and command list, so
each banner is written as an RGBA4 file built from that template (no ETC1 encoder needed).
"""
from __future__ import annotations

import struct

import numpy as np

from . import texture
from .fontimport import Font

# ---------------------------------------------------------------------- drawing

ALPHA_INK = 40          # alpha above this counts as text (fill or outline)
CAP_RATIO = 0.74        # English capital height / Japanese glyph height
SHADOW = 0.85           # opacity of the shadow (the outline, 1 px down and right)


def _mask(font: Font, text: str, bold: int = 1, level: int = 0) -> np.ndarray:
    """Coverage 0..1 (cell height x width) of `text` in `font`, widened by `bold` pixels.
    level: glyph values up to this are not text (2D14SH: 8 is its own outline)."""
    width = sum(font.width(font.cmap[ord(c)])[2] if ord(c) in font.cmap else 4 for c in text) + bold + 2
    m = np.zeros((font.ch, width), np.float32)
    x = 1
    for c in text:
        gi = font.cmap.get(ord(c))
        if gi is None:
            x += 4
            continue
        left, gw, adv = font.width(gi)
        g = np.clip((np.array([list(r[:gw]) for r in font.glyph(gi)], np.float32) - level) / (15.0 - level), 0, 1)
        x0 = max(0, x + left)
        w = min(g.shape[1], m.shape[1] - x0)
        m[:g.shape[0], x0:x0 + w] = np.maximum(m[:g.shape[0], x0:x0 + w], g[:, :w])
        x += adv
    for _ in range(bold):
        m[:, 1:] = np.maximum(m[:, 1:], m[:, :-1].copy())
    cols = np.where(m.max(0) > 0.3)[0]
    if not len(cols):
        return m[:, :0]
    return m[:, cols[0]:cols[-1] + 1]          # full cell height: the baseline stays put


def _cap_rows(font: Font, level: int = 0) -> tuple[int, int]:
    """(top, bottom) cell rows of a capital letter (cap height above the baseline)."""
    rows = [i for i, r in enumerate(font.glyph(font.cmap[ord("H")])) if any(v > max(4, level) for v in r)]
    return rows[0], rows[-1]


def _narrow(m: np.ndarray, width: int) -> np.ndarray:
    """Resample the columns (linear) to `width`."""
    if m.shape[1] <= width:
        return m
    xs = np.linspace(0, m.shape[1] - 1, width)
    i0 = np.floor(xs).astype(int)
    i1 = np.minimum(i0 + 1, m.shape[1] - 1)
    f = (xs - i0)[None, :]
    return m[:, i0] * (1 - f) + m[:, i1] * f


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    h, w = m.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            src = m[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
            dst = out[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)]
            np.maximum(dst, src, out=dst)
    return out


def restyle(rgba: np.ndarray, text: str, font: Font, align: str | None = None,
            margin: int = 2, level: int = 0, cap_px: float | None = None,
            max_width: int | None = None) -> np.ndarray:
    """The image with its text replaced by `text` (same style); see the module doc."""
    a = rgba[..., 3].astype(int)
    ink = a > ALPHA_INK
    if not ink.any():
        raise ValueError("texture label: no text in the image")
    luma = rgba[..., :3].astype(int) @ np.array([299, 587, 114]) // 1000
    lv = luma[ink]
    cut = (lv.min() + lv.max()) / 2
    fill = ink & (luma > cut)
    edge = ink & ~fill
    H, W = a.shape
    # fill colour per row (rows without fill: the nearest row that has some)
    rows = [y for y in range(H) if fill[y].any()]
    grad = np.zeros((H, 4), np.float32)
    for y in range(H):
        ny = min(rows, key=lambda r: abs(r - y))
        grad[y] = rgba[ny][fill[ny]].mean(0)
    edge_col = rgba[edge].mean(0) if edge.any() else np.array([40, 20, 0, 255], np.float32)
    ys, xs = np.where(ink)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    if align is None:
        centre = (left + right) / 2
        align = "center" if abs(centre - (W - 1) / 2) < W * 0.08 else "left"
    # the English capitals as tall as CAP_RATIO of the Japanese glyphs (the font is scaled
    # up smoothly when it is smaller), centred on them
    frows = np.where(fill.any(1))[0]
    cap_top, cap_bottom = _cap_rows(font, level)
    target = cap_px if cap_px else (frows[-1] - frows[0] + 1) * CAP_RATIO
    scale = target / (cap_bottom - cap_top + 1)
    cell = _mask(font, text, bold=0 if scale > 1.2 or level else 1, level=level)
    if abs(scale - 1.0) > 0.02:
        cell = _resize(cell, scale)
        cap_top, cap_bottom = cap_top * scale, (cap_bottom + 1) * scale - 1
    m = _narrow(cell, min(W - 2 * margin - 3, max_width or W))
    fy = round((frows[0] + frows[-1]) / 2 - (cap_top + cap_bottom) / 2)
    rows = np.where(m.max(1) > 0.05)[0]
    m = m[rows[0]:rows[-1] + 1]
    fy += rows[0]
    fy = max(1, min(H - m.shape[0] - 2, fy))
    m = m[:H - fy - 2]
    h, w = m.shape
    fx = round((W - w - 1) / 2) if align == "center" else max(1, left)
    full = np.zeros((H, W), np.float32)
    # firm strokes after scaling up (the faint bar of "e" stays); small text keeps its counters
    full[fy:fy + h, fx:fx + w] = np.clip(m / 0.35, 0, 1) if scale > 1 else np.clip((m - 0.15) / 0.6, 0, 1)
    outline = _dilate(np.clip(full * 2, 0, 1))
    shadow = np.zeros_like(outline)
    shadow[1:, 1:] = outline[:-1, :-1] * SHADOW
    back = np.maximum(outline, shadow)                   # outline and shadow: the edge colour
    out = np.zeros(rgba.shape, np.float32)
    k = full[..., None]
    out[..., :3] = edge_col[:3] * (1 - k) + grad[:, None, :3] * k
    out[..., 3] = np.clip(np.maximum(back, full) * edge_col[3] + full * (255 - edge_col[3]), 0, 255)
    out[..., :3] = np.where(out[..., 3:] > 0, out[..., :3], 0)
    return out.round().astype(np.uint8)


def restyle_pill(rgba: np.ndarray, text: str, font: Font, level: int = 0, cap_px: float = 8,
                 margin: int = 2, pad_right: int = 0) -> np.ndarray:
    """A label on a dark rounded bar (quest status): the bar is drawn again over the whole
    width (the English is longer), the text centred on it, outlined in the bar colour.
    pad_right: columns kept empty on the right (the image's end lies under a frame)."""
    H, W = rgba.shape[:2]
    a = rgba[..., 3].astype(int)
    luma = rgba[..., :3].astype(int) @ np.array([299, 587, 114]) // 1000
    solid = a > 200
    lv = luma[solid]
    dark = solid & (luma <= (lv.min() + lv.max()) / 2)
    rows = np.where(dark.sum(1) >= W // 4)[0]
    top, bottom = rows[0], rows[-1]
    colour = np.median(rgba[dark], axis=0)
    # the bar: rounded ends (radius half its height), antialiased by 4x4 supersampling
    ss = 4
    yy, xx = np.mgrid[0:H * ss, 0:W * ss] / ss
    r = (bottom - top + 1) / 2
    cy = top + r
    x0, x1 = margin + r, W - pad_right - margin - r
    dx = np.maximum(0, np.maximum(x0 - xx, xx - x1))
    inside = (dx ** 2 + (yy - cy) ** 2) <= r ** 2
    cover = inside.reshape(H, ss, W, ss).mean((1, 3))
    bar = np.zeros((H, W, 4), np.float32)
    bar[..., :3] = colour[:3]
    bar[..., 3] = cover * 255
    txt = np.zeros((H, W, 4), np.float32)
    txt[:, :W - pad_right] = restyle(rgba[:, :W - pad_right], text, font, "center", margin=margin + 1,
                                     level=level, cap_px=cap_px)
    ta = txt[..., 3:] / 255
    out = np.empty_like(bar)
    out[..., 3] = txt[..., 3] + bar[..., 3] * (1 - ta[..., 0])
    na = np.maximum(out[..., 3:], 1e-6)
    out[..., :3] = (txt[..., :3] * txt[..., 3:] + bar[..., :3] * bar[..., 3:] * (1 - ta)) / na
    return out.clip(0, 255).round().astype(np.uint8)


def _resize(m: np.ndarray, scale: float) -> np.ndarray:
    from PIL import Image
    im = Image.fromarray((m * 255).astype(np.uint8))
    size = (max(1, round(m.shape[1] * scale)), max(1, round(m.shape[0] * scale)))
    return np.asarray(im.resize(size, Image.BICUBIC), np.float32).clip(0, 255) / 255.0


# ---------------------------------------------------------------------- town banners

BANNER_SIZE = (256, 32)
BANNER_DATA = 0x280                 # pixel data offset (both kinds)
PICA_RGBA4, PICA_ETC1A4 = 4, 13
BANNER_FORMAT_AT = 0x1E0 + 0x18     # texture unit 0 type register value
# official PC names (Menu_Region / place names)
BANNERS = {
    "location_b002_001a.bch": "Heliodor",
    "location_b002_002a.bch": "Downtown Heliodor",
    "location_b005_001a.bch": "Gallopolis",
    "location_b009_001a.bch": "Sniflheim",
    "location_b011_001a.bch": "Cobblestone",
    "location_b012_001a.bch": "Gondolia",
    "location_b014_001a.bch": "Puerto Valor",
    "location_b015_001a.bch": "Octagonia",
    "location_b016_001a.bch": "Tickington",
    "location_b017_001a.bch": "Lonalulu",
    "location_b018_001a.bch": "Nautica",
    "location_b019_001a.bch": "Phnom Nonh",
    "location_b020_001a.bch": "Angri-La",
    "location_b021_001a.bch": "Hotto",
    "location_b022_001a.bch": "Arboria",
    "location_b023_001a.bch": "Havens Above",
    "location_b023_001b.bch": "???",
    "location_d002_001a.bch": "The Kingsbarrow",
    "location_f010_001a.bch": "Drustan's Labyrinth",
    "location_f010_002a.bch": "Trial Isle",
    "location_f010_003a.bch": "The Disciple's Trial",
    "location_f010_004a.bch": "The Sage's Trial",
    "location_f010_005a.bch": "The Luminary's Trial",
    "location_m001_001a.bch": "Ruins of Dundrasil",
    "location_m004_001a.bch": "L'Académie de Notre Maître des Médailles",
    "location_m014_001a.bch": "Yggdrasil",
    "location_m024_001a.bch": "The Tower of Lost Time",
    "location_m024_002a.bch": "Altar of Ages",
    "location_m025_001a.bch": "Mysterious Ruins",
}


def _fake_flim(raw: bytes, w: int, h: int, fmt: int) -> bytes:
    foot = bytearray(0x28)
    foot[:4] = b"FLIM"
    foot[0x14:0x18] = b"imag"
    struct.pack_into("<HHHBB", foot, 0x1C, w, h, 0x80, fmt, 0)
    return raw + bytes(foot)


def banner_pixels(data: bytes) -> np.ndarray:
    w, h = BANNER_SIZE
    fmt = struct.unpack_from("<I", data, BANNER_FORMAT_AT)[0]
    if fmt == PICA_RGBA4:
        return texture.decode(_fake_flim(data[BANNER_DATA:BANNER_DATA + w * h * 2], w, h, 8))
    if fmt == PICA_ETC1A4:
        return texture.decode(_fake_flim(data[BANNER_DATA:BANNER_DATA + w * h], w, h, 11))
    raise ValueError(f"town banner: texture format {fmt}")


def banner_file(rgba: np.ndarray, template: bytes) -> bytes:
    """An RGBA4 banner file (`template`: any RGBA4 banner) with these pixels."""
    w, h = BANNER_SIZE
    if struct.unpack_from("<I", template, BANNER_FORMAT_AT)[0] != PICA_RGBA4:
        raise ValueError("town banner template must be RGBA4")
    n = w * h * 2
    flim = texture.encode(rgba, _fake_flim(bytes(n), w, h, 8))
    return template[:BANNER_DATA] + flim[:n] + template[BANNER_DATA + n:]


def patch_banners(files: dict[str, bytes], font_data: bytes) -> dict[str, bytes]:
    """{file name: data} of ui/location -> the English banners."""
    font = Font(font_data)
    template = next((d for d in files.values()
                     if struct.unpack_from("<I", d, BANNER_FORMAT_AT)[0] == PICA_RGBA4), None)
    if template is None:
        raise ValueError("town banners: no RGBA4 banner to use as template")
    out = {}
    for name, text in BANNERS.items():
        if name in files:
            px = restyle(banner_pixels(files[name]), text, font, align="center", level=FONT_LEVEL)
            out[name] = banner_file(px, template)
    return out


# ---------------------------------------------------------------------- layout images

FONT = "2D14SH"         # bold font of the 2D titles; its glyph values up to FONT_LEVEL are its
FONT_LEVEL = 9          # own shadow/outline, the fill is above
# texture name -> (English, kind, options). The map hints sit after the START icon:
# menu_map_scroll1 shows txt1 ("Map scroll"), menu_map_scroll2 txt1 and txt2 next to each
# other ("Map scroll" "off"): txt1's English must end before txt2 starts (80 px). Quest status
# (quest list, 64 px badges): PC Menu_Quest_Progress "Underway" / "Completed!" / "Nearly
# There!" - the last two are too long for the badge. The list frame covers the badge's last
# columns (Underway touched it, 2026-10-15): pad_right.
TEXTURE_LABELS = {
    "timg/menu_map_txt1.bflim": ("Map scroll", "label", {"align": "left", "cap_px": 9, "max_width": 80}),
    "timg/menu_map_txt2.bflim": ("off", "label", {"align": "left", "cap_px": 9}),
    "timg/win_movie_txt_3d.bflim": ("Hold to Skip", "label", {"cap_px": 9}),   # PC Menu_Skip_000_000
    "timg/win_movie_txt_2d.bflim": ("Hold to Skip", "label", {"cap_px": 9}),
    "timg/icon_quest_order.bflim": ("Underway", "pill", {"cap_px": 8, "pad_right": 5}),
    "timg/icon_quest_clear.bflim": ("Cleared", "pill", {"cap_px": 8, "pad_right": 5}),
    "timg/icon_quest_achievementtga.bflim": ("Achieved", "pill", {"cap_px": 8, "pad_right": 5}),
}
# Texture atlases: a label made of pieces of the image (from the layout's pic panes: texture
# coordinates; a piece with k is shown turned by 90 degrees k times), joined left to right into
# one strip; the first `icon` pixels (a button icon) stay. 3D field map (bottom screen,
# menu_map_*_3d.bflyt): "Close map" / "Swap map" (Ⓨ), "Show/Hide Objective" (Ⓡ, 64 px + a
# 96 px piece stored upright along the left edge: menu_map_monster2, rotated 90 degrees).
ATLAS_LABELS = {
    "timg/menu_map_frame_3d.bflim": [
        ([(32, 0, 128, 16, 0)], 17, "Close map"),                       # 地図を閉じる
        ([(32, 16, 128, 32, 0)], 17, "Swap map"),                        # 地図切り替え (40490)
        ([(64, 96, 128, 112, 0), (0, 16, 16, 112, 1)], 17, "Show/Hide Objective"),   # 40679
    ],
}
PACKS = {"common_2d.pack", "common_2way.pack", "common_3d.pack", "record_3d.pack", "title.pack",
         "dive_dugeon_2way.pack"}


def redraw_atlas(bflim: bytes, labels: list, font: Font) -> bytes:
    px = texture.decode(bflim).copy()
    for pieces, icon, text in labels:
        strip = np.concatenate([np.rot90(px[y0:y1, x0:x1], k) for x0, y0, x1, y1, k in pieces], 1)
        strip = np.ascontiguousarray(strip)
        strip[:, icon:] = restyle(strip[:, icon:], text, font, "left", margin=1, level=FONT_LEVEL, cap_px=9)
        x = 0
        for x0, y0, x1, y1, k in pieces:
            w = (x1 - x0) if k % 2 == 0 else (y1 - y0)
            px[y0:y1, x0:x1] = np.rot90(strip[:, x:x + w], -k)
            x += w
    return texture.encode(px, bflim)


def redraw(bflim: bytes, text: str, kind: str, font: Font, **opt) -> bytes:
    px = texture.decode(bflim)
    if kind == "pill":
        new = restyle_pill(px, text, font, level=FONT_LEVEL, **opt)
    else:
        new = restyle(px, text, font, level=FONT_LEVEL, **opt)
    return _encode(new, bflim)


def _encode(px: np.ndarray, bflim: bytes) -> bytes:
    if texture.FORMATS.get(texture.info(bflim)["format"], ("",))[0] in ("RGBA4", "RGBA8", "LA8", "LA4", "A8",
                                                                      "RGB565", "RGBA5551"):
        return texture.encode(px, bflim)
    return texture.encode_rgba4(px, bflim)       # ETC1 / ETC1A4


def patch_pack(pack_data: bytes, font_data: bytes) -> bytes | None:
    """The pack with its TEXTURE_LABELS in English, or None if it has none."""
    from .darc import Sarc
    from .pack import Pack
    font = Font(font_data)
    pack = Pack.parse(pack_data)
    changed = False
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        repl = {}
        for n in sarc.nodes:
            if n.name in ATLAS_LABELS:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw_atlas(raw, ATLAS_LABELS[n.name], font)
            elif n.name in TEXTURE_LABELS:
                text, kind, opt = TEXTURE_LABELS[n.name]
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw(raw, text, kind, font, **opt)
        if repl:
            e.data = sarc.rebuild(repl)
            changed = True
    return pack.build() if changed else None
