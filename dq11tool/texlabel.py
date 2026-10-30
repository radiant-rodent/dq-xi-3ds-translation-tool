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


def _mask(font: Font, text: str, bold: int = 1, level: int = 0, spacing: int = 0) -> np.ndarray:
    """Coverage 0..1 (cell height x width) of `text` in `font`, widened by `bold` pixels.
    level: glyph values up to this are not text (2D14SH: 8 is its own outline)."""
    width = sum(font.width(font.cmap[ord(c)])[2] if ord(c) in font.cmap else 4 for c in text) + bold + 2 + spacing * len(text)
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
        x += adv + spacing
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
            max_width: int | None = None, outline: int = 1, bold: int = 0, spacing: int = 0,
            baseline: int | None = None) -> np.ndarray:
    """The image with its text replaced by `text` (same style); see the module doc.
    An empty text clears the image; outline: its width in pixels (big titles: 2)."""
    if not text:
        return np.zeros_like(rgba)
    a = rgba[..., 3].astype(int)
    ink = a > ALPHA_INK
    if not ink.any():
        raise ValueError("texture label: no text in the image")
    luma = rgba[..., :3].astype(int) @ np.array([299, 587, 114]) // 1000
    lv = luma[ink]
    cut = (lv.min() + lv.max()) / 2
    fill = ink & (luma > cut)
    if not fill.any():                     # one flat colour: it is all fill
        fill = ink
    edge = ink & ~fill
    had_edge = bool(edge.any())            # a flat white mask (tinted by the layout) has no outline
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
    cell = _mask(font, text, bold=0 if scale > 1.2 or level else 1, level=level, spacing=spacing)
    if abs(scale - 1.0) > 0.02:
        cell = _resize(cell, scale)
        cap_top, cap_bottom = cap_top * scale, (cap_bottom + 1) * scale - 1
    for _ in range(bold):                  # thicker strokes (small labels with 2 px Japanese strokes)
        cell = np.concatenate([cell, np.zeros((cell.shape[0], 1), cell.dtype)], 1)
        cell[:, 1:] = np.maximum(cell[:, 1:], cell[:, :-1])
    m = _narrow(cell, min(W - 2 * margin - 3, max_width or W))
    fy = round((frows[0] + frows[-1]) / 2 - (cap_top + cap_bottom) / 2)
    if baseline is not None:               # words that sit side by side share one baseline
        fy = round(baseline - cap_bottom)
    rows = np.where(m.max(1) > 0.05)[0]
    m = m[rows[0]:rows[-1] + 1]
    fy += rows[0]
    fy = max(1, min(H - m.shape[0] - 2, fy))
    m = m[:H - fy - 2]
    h, w = m.shape
    fx = round((W - w - 1) / 2) if align == "center" else max(1, right + 1 - w) if align == "right" else max(1, left)
    full = np.zeros((H, W), np.float32)
    # firm strokes after scaling up (the faint bar of "e" stays); small text keeps its counters
    full[fy:fy + h, fx:fx + w] = np.clip(m / 0.35, 0, 1) if scale > 1 else np.clip((m - 0.15) / 0.6, 0, 1)
    edge = np.clip(full * 2, 0, 1)
    for _ in range(outline):
        edge = _dilate(edge)
    outline = edge
    shadow = np.zeros_like(outline)
    shadow[1:, 1:] = outline[:-1, :-1] * SHADOW
    back = np.maximum(outline, shadow) if had_edge else full   # outline and shadow: the edge colour
    out = np.zeros(rgba.shape, np.float32)
    k = full[..., None]
    out[..., :3] = edge_col[:3] * (1 - k) + grad[:, None, :3] * k
    out[..., 3] = np.clip(np.maximum(back, full) * edge_col[3] + full * (255 - edge_col[3]), 0, 255)
    out[..., :3] = np.where(out[..., 3:] > 0, out[..., :3], 0)
    return out.round().astype(np.uint8)


def _grow_step(m: np.ndarray, cross: bool) -> np.ndarray:
    if not cross:
        return _dilate(m.astype(np.float32)) > 0
    g = m.copy()
    g[1:] |= m[:-1]
    g[:-1] |= m[1:]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    return g


def _grow(m: np.ndarray, n: int) -> np.ndarray:
    """Binary dilation by n pixels, square and cross steps alternating (round-ish rings)."""
    for i in range(n):
        m = _grow_step(m, i % 2 == 1)
    return m


def _depth(m: np.ndarray) -> np.ndarray:
    """Per pixel of the binary mask: its ring counted from the outside (1 = the border)."""
    d = np.zeros(m.shape, np.int32)
    i = 0
    while m.any():
        d += m
        m = m & ~_grow_step(~m, i % 2 == 1)
        i += 1
    return d


def vector_font() -> str | None:
    """A TrueType font for big lettering (scaled bitmap glyphs turn blobby), if there is one."""
    import os
    path = os.environ.get("DQ11_VECTOR_FONT") or os.path.join(os.path.dirname(__file__), "data", "lettering.ttf")
    return path if os.path.exists(path) else None


def _vector_mask(text: str, cap_px: float, spacing: float = 0) -> np.ndarray:
    """Coverage 0..1 of `text` in the vector font, capitals `cap_px` tall (4x supersampled),
    `spacing` pixels added between the letters."""
    from PIL import Image, ImageDraw, ImageFont
    probe = ImageFont.truetype(vector_font(), 400)
    l, t, r, b = probe.getbbox("H")
    size = 400 * cap_px * 4 / (b - t)
    f = ImageFont.truetype(vector_font(), round(size))
    l, t, r, b = f.getbbox(text)
    im = Image.new("L", (round(r - l + 16 + spacing * 4 * len(text)), b - t + 16))
    x = 8 - l
    for c in text:
        ImageDraw.Draw(im).text((x, 8 - t), c, font=f, fill=255)
        x += f.getlength(c) + spacing * 4
    im = im.resize((max(1, round(im.width / 4)), max(1, round(im.height / 4))), Image.LANCZOS)
    m = np.asarray(im, np.float32) / 255
    cols = np.where(m.max(0) > 0.3)[0]
    return m[:, cols[0]:cols[-1] + 1]


def _blur(m: np.ndarray, r: int) -> np.ndarray:
    for axis in (0, 1):                         # box blur, twice: close to a gaussian
        for _ in range(2):
            pad = np.pad(m, [(r, r) if a == axis else (0, 0) for a in (0, 1)])
            c = np.cumsum(pad, axis, dtype=np.float32)
            c = np.concatenate([np.zeros_like(c.take([0], axis)), c], axis)
            n = m.shape[axis]
            m = (c.take(range(2 * r + 1, 2 * r + 1 + n), axis) - c.take(range(n), axis)) / (2 * r + 1)
    return m


def restyle_glow(rgba: np.ndarray, text: str, font: Font, cap_px: float, radius: int = 2) -> np.ndarray:
    """Light lettering with a soft glow around it (the roulette display): the letter colour is
    the brightest opaque pixels', the glow's colour and strength its halo's."""
    a = rgba[..., 3].astype(int)
    ys, xs = np.where(a > ALPHA_INK)
    luma = rgba[..., :3].astype(int) @ np.array([299, 587, 114]) // 1000
    solid = a > 200
    fill = rgba[solid & (luma >= np.percentile(luma[solid], 70))][:, :3].mean(0)
    halo = (a > 10) & (a < 160)
    glow_rgb = rgba[halo][:, :3].mean(0)
    peak = np.percentile(a[halo], 90)
    H, W = a.shape
    m = _vector_mask(text, cap_px) if vector_font() else _resize(
        _mask(font, text, bold=0, level=FONT_LEVEL), cap_px / (np.subtract(*_cap_rows(font, FONT_LEVEL)[::-1]) + 1))
    m = _narrow(m, W - 4 * radius - 2)
    rows = np.where(m.max(1) > 0.05)[0]
    m = m[rows[0]:rows[-1] + 1]
    h, w = m.shape
    cy = max(0, min(H - h, round((ys.min() + ys.max() - h) / 2)))
    cx = max(0, min(W - w, round((W - w) / 2)))
    full = np.zeros((H, W), np.float32)
    full[cy:cy + h, cx:cx + w] = np.clip((m - 0.2) / 0.6, 0, 1)
    glow = np.clip(_blur(_grow(full > 0.3, 1).astype(np.float32), radius) * 2.2, 0, 1) * peak / 255
    alpha = full + glow * (1 - full)
    out = np.zeros_like(rgba)
    col = (fill[None, None] * full[..., None] + glow_rgb[None, None] * (glow * (1 - full))[..., None]) / np.maximum(alpha, 1e-6)[..., None]
    out[..., :3] = np.clip(col, 0, 255).round()
    out[..., 3] = np.clip(alpha * 255, 0, 255).round()
    return out


def restyle_rings(rgba: np.ndarray, text: str, font: Font, rings: int, align: str | None = "center",
                  cap_px: float | None = None, margin: int = 0, draw: int | None = None,
                  at: tuple | None = None, bright: bool = False) -> np.ndarray:
    """Big decorated lettering (あたり!, ゴール!!): the glyphs are a core with rings of colour
    around it (dark edge, a coloured band, a light line). The colour of each ring, row by row,
    is measured on the Japanese glyphs (ring = distance from the outside, `rings` of them); the
    English core (the font's fill, scaled, crisp) gets `draw` rings (default: as many) sampled
    from them - thin Latin strokes need thinner rings, or the counters fill up.
    at: (x, bottom row) of the English core instead of centring it on the old glyphs.
    bright: the core (and light line) in the brightest colour of each row (ジャックポット: the
    kana are cut into gold plates, their own core is the dark cut)."""
    draw = draw or rings
    if not text:
        return np.zeros_like(rgba)
    H, W = rgba.shape[:2]
    sil = rgba[..., 3] > ALPHA_INK
    ys, xs = np.where(sil)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    d = np.minimum(_depth(sil), rings + 1)
    prof = np.zeros((rings + 2, H, 4), np.float32)     # ring (rings + 1: core) x row -> colour
    for k in range(1, rings + 2):
        rows = [y for y in range(H) if (d[y] == k).any()]
        if not rows:
            prof[k] = prof[k - 1]
            continue
        sums = np.array([rgba[y][d[y] == k].astype(np.float32).sum(0) for y in range(H)])
        counts = np.array([(d[y] == k).sum() for y in range(H)], np.float32)
        for y in range(H):                     # 11 rows averaged: no stripes from thin rows
            ny = min(rows, key=lambda r: abs(r - y))
            lo, hi = max(0, ny - 5), ny + 6
            prof[k, y] = sums[lo:hi].sum(0) / max(1, counts[lo:hi].sum())
    # drawn: `draw` rings outside the English core, spread over the measured ones but the last
    # (the light line, drawn inside the core's edge so the counters stay open), then the core
    pick = [1 + round(j * (rings - 2) / max(1, draw - 1)) for j in range(draw)] + [rings, rings + 1]
    prof = np.concatenate([prof[:1], prof[pick]])
    if bright:                                 # glyphs cut into a plate: the core is the plate's
        luma = rgba[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        top_px = sil & (luma >= np.percentile(luma[sil], 75))
        rows = [y for y in range(H) if top_px[y].any()]
        for y in range(H):
            ny = min(rows, key=lambda r: abs(r - y))
            lo, hi = max(0, ny - 3), ny + 4
            prof[-1, y] = prof[-2, y] = rgba[lo:hi][top_px[lo:hi]].astype(np.float32).mean(0)
    rings = draw
    cap_top, cap_bottom = _cap_rows(font, FONT_LEVEL)
    target = cap_px if cap_px else (bottom - top + 1 - 2 * rings) * CAP_RATIO
    if vector_font():
        m = _vector_mask(text, target, spacing=2 * rings + 2)     # 2 px of background between outlines
    else:
        m = _resize(_mask(font, text, bold=0, level=FONT_LEVEL), target / (cap_bottom - cap_top + 1))
    m = _narrow(m, W - 2 * rings - 2 * margin - 2)
    core = m > 0.5
    rows = np.where(core.any(1))[0]
    core = core[rows[0]:rows[-1] + 1]
    h, w = core.shape
    cy = max(rings + 1, min(H - h - rings - 1, round((top + bottom - h) / 2)))
    if align == "center":
        cx = round((W - w) / 2)
    elif align == "right":
        cx = right - rings - w + 1
    else:
        cx = left + rings
    cx = max(rings + 1, min(W - w - rings - 1, cx))
    if at:
        cx, cy = at[0], at[1] - h + 1
    full = np.zeros((H, W), bool)
    full[cy:cy + h, cx:cx + w] = core
    new = _grow(full, rings)
    dn = np.minimum(_depth(new), rings + 1)
    dn[full] = rings + np.minimum(_depth(full), 2)[full]     # light line, then the core
    # rows of the new lettering map onto the old glyph rows (the gradient spans both)
    ny0, ny1 = cy - rings, cy + h + rings - 1
    src = np.clip(np.round(top + (np.arange(H) - ny0) * (bottom - top) / max(1, ny1 - ny0)), 0, H - 1).astype(int)
    out = np.zeros_like(rgba)
    yy, xx = np.where(new)
    out[yy, xx] = prof[dn[yy, xx], src[yy]].round().astype(np.uint8)
    return out


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


def restyle_badge(rgba: np.ndarray, text: str, font: Font, cap_px: float = 8.0) -> np.ndarray:
    """A party member initial badge (equipment UI indicator): circular blue background
    with an orange letter outlined in black."""
    circle_mask = rgba[:, :, 3] > 50
    bg = np.zeros_like(rgba)
    bg[circle_mask, :3] = [85, 102, 136]
    bg[:, :, 3] = rgba[:, :, 3]

    m = _mask(font, text, bold=1, level=FONT_LEVEL)
    top, bottom = _cap_rows(font, level=FONT_LEVEL)
    cap = bottom - top + 1
    m = _resize(m, cap_px / cap)
    cols = np.where(m.max(0) > 0.3)[0]
    rows = np.where(m.max(1) > 0.3)[0]
    m = m[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    edge = _dilate(m)

    mh, mw = m.shape
    y0 = 8 - mh // 2
    x0 = 16 - mw // 2

    out = bg.copy()
    for dy in range(edge.shape[0]):
        for dx in range(edge.shape[1]):
            if edge[dy, dx] > 0.2:
                out[y0 + dy, x0 + dx, :3] = [0, 0, 0]
    for dy in range(mh):
        for dx in range(mw):
            v = m[dy, dx]
            if v > 0.2:
                out[y0 + dy, x0 + dx, :3] = (np.array([255, 187, 51]) * v + out[y0 + dy, x0 + dx, :3] * (1 - v)).astype(np.uint8)
    return out


def _scaled_mask(font: Font, text: str, cap_px: float | None, level: int = 0, bold: int | None = None,
                 spacing: int = 0) -> tuple[np.ndarray, float]:
    """Coverage of `text` with capitals cap_px tall (None: the font's own size, no resampling),
    cropped to its rows, and the row of the capitals' middle in it."""
    cap_top, cap_bottom = _cap_rows(font, level)
    scale = 1.0 if cap_px is None else cap_px / (cap_bottom - cap_top + 1)
    if bold is None:
        bold = 0 if scale > 1.2 or level else 1
    m = _mask(font, text, bold=bold, level=level, spacing=spacing)
    if abs(scale - 1.0) > 0.02:
        m = _resize(m, scale)
        cap_top, cap_bottom = cap_top * scale, (cap_bottom + 1) * scale - 1
    m = np.clip(m / 0.35, 0, 1) if scale > 1 else np.clip((m - 0.15) / 0.6, 0, 1)
    rows = np.where(m.max(1) > 0.05)[0]
    return m[rows[0]:rows[-1] + 1], (cap_top + cap_bottom) / 2 - rows[0]


def restyle_button(rgba: np.ndarray, rect: tuple, text: str, font: Font, cap_px: float | None = 9,
                   align: str = "left", level: int | None = None, bold: int | None = None) -> np.ndarray:
    """Light text on a dark bar (casino buttons, the race's Dash label): inside rect (x0, y0,
    x1, y1: the text area, between the button icon and the bar's end) the old text is painted
    over with the bar colour of its row and `text` drawn in the old text's colour."""
    out = rgba.astype(np.float32)
    x0, y0, x1, y1 = rect
    pad = 2                                     # the fringe of the old text may reach out of the rectangle sideways
    area = out[y0:y1, x0 - pad:x1 + pad]
    inner = np.zeros(area.shape[:2], bool)
    inner[:, pad:-pad] = True
    luma = area[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
    med = np.median(luma[:, pad:-pad], 1, keepdims=True)          # the bar's brightness per row
    # text pixels are pale beige (blue not far below red): the orange frame and bar ends are not
    textlike = area[..., 2] > 0.35 * area[..., 0]
    old = inner & textlike & (luma > med + 40)  # the text itself: its box and colour
    if not old.any():
        raise ValueError("button label: no text in the rectangle")
    ys, xs = np.where(old)
    lv = luma[old]
    colour = area[old][lv >= np.percentile(lv, 75)].mean(0)
    # everything that is even slightly lighter than the bar is painted over (antialiased fringe)
    cover = _dilate(_dilate((inner & textlike & (luma > med + 8) | old).astype(np.float32))) > 0
    for y in range(area.shape[0]):
        if cover[y].any():
            free = ~cover[y] & inner[y]            # the bar between the letters, not the frame
            area[y, cover[y]] = np.median(area[y, free], 0) if free.any() else area[y].min(0)
    if not text:                               # only paint the old text over
        return out.clip(0, 255).round().astype(np.uint8)
    m, mid = _scaled_mask(font, text, cap_px, FONT_LEVEL if level is None else level, bold)
    m = _narrow(m, area.shape[1] - 2)
    h, w = m.shape
    fy = max(0, min(area.shape[0] - h, round((ys.min() + ys.max()) / 2 - mid)))
    fx = max(1, xs.min()) if align == "left" else (area.shape[1] - w) // 2
    fx = min(fx, area.shape[1] - w - 1)
    k = m[..., None]
    area[fy:fy + h, fx:fx + w] = area[fy:fy + h, fx:fx + w] * (1 - k) + colour * k
    return out.clip(0, 255).round().astype(np.uint8)


def _solid(m: np.ndarray, close: int = 2) -> np.ndarray:
    """m with openings up to `close` px closed and its holes filled (the shape a glow goes round)."""
    p = close + 1
    m = ~_grow(~_grow(np.pad(m, p), close), close)[p:-p, p:-p]
    outside = np.zeros_like(m)
    outside[[0, -1], :] = ~m[[0, -1], :]
    outside[:, [0, -1]] |= ~m[:, [0, -1]]
    while True:                                    # flood the background from the border
        grown = _grow_step(outside, True) & ~m
        if (grown == outside).all():
            return ~outside
        outside = grown


def redraw_glow(glow: np.ndarray, label: np.ndarray, icon: int, old_label: np.ndarray) -> np.ndarray:
    """The race's Dash flash (gauge_bar_f3): a soft glow around the outline of its label (the
    label is half as tall and sits in the middle). The glow's strength and colour by distance
    from the old label's shape are measured on the old flash and laid around the new label's
    shape (outline included, openings closed: it goes round the word, not into its letters).
    Columns before `icon` keep the old glow of the button icon; the new one blends into it."""
    H, W = glow.shape[:2]
    dy = (H - label.shape[0]) // 2
    gy, gx = np.mgrid[0:H, 0:W]

    def distance(lab):
        shape = np.zeros((H, W), bool)
        a = lab[..., 3] > 60
        for part in (slice(0, icon), slice(icon, None)):          # icon and text apart
            m = np.zeros_like(a)
            m[:, part] = a[:, part]
            shape[dy:dy + lab.shape[0], :lab.shape[1]] |= _solid(m)
        d = np.full((H, W), 1e9, np.float32)
        for y, x in zip(*np.nonzero(shape)):
            np.minimum(d, np.hypot(gy - y, gx - x), out=d)
        d[shape] = 0
        return d
    old = glow.astype(np.float32)
    bins = np.round(distance(old_label) * 2).astype(int)
    n = 16
    have = np.array([(bins == i).any() for i in range(n)])
    alpha = np.array([old[..., 3][bins == i].mean() if have[i] else 0 for i in range(n)])
    rgb = np.array([(old[..., :3][bins == i] * old[..., 3][bins == i][:, None]).sum(0)
                    / max(old[..., 3][bins == i].sum(), 1) if have[i] else (0, 0, 0) for i in range(n)])
    idx = np.arange(n)
    alpha = np.interp(idx, idx[have], alpha[have])
    rgb = np.stack([np.interp(idx, idx[have], rgb[have][:, c]) for c in range(3)], -1)
    new_bins = np.round(distance(label) * 2).astype(int)
    far = new_bins >= n
    new_bins = np.minimum(new_bins, n - 1)
    out = np.zeros_like(old)
    out[..., :3] = rgb[new_bins]
    out[..., 3] = np.where(far, 0, alpha[new_bins])
    keep = old[:, :icon, 3] > out[:, :icon, 3]
    out[:, :icon][keep] = old[:, :icon][keep]
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
# small labels (buttons, captions, the payout table) use the menu font at its own size: scaling
# a bigger font down to ~9 px blurs every glyph
SMALL_FONT = "2D9"
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
    "timg/kaji2_txt1.bflim": ("Primed!", "label", {"align": "center", "cap_px": 14}),
    # Equipment UI party member equipped badges: Hero, Erik, Veronica, Serena, Sylvando, Rab, Jade, Hendrik
    "timg/icon_charaE_pc1.bflim": ("H", "badge", {}),
    "timg/icon_charaE_pc2.bflim": ("E", "badge", {}),
    "timg/icon_charaE_pc3.bflim": ("V", "badge", {}),
    "timg/icon_charaE_pc4.bflim": ("S", "badge", {}),
    "timg/icon_charaE_pc5.bflim": ("S", "badge", {}),
    "timg/icon_charaE_pc6.bflim": ("R", "badge", {}),
    "timg/icon_charaE_pc7.bflim": ("J", "badge", {}),
    "timg/icon_charaE_pc8.bflim": ("H", "badge", {}),
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
    "timg/skill_up_panel_3d.bflim": [
        ([(384, 0, 512, 16, 0)], 0, "Pep Power Learned!", {"align": "center", "cap_px": 9}),
        ([(384, 16, 512, 32, 0)], 0, "Pep Power!", {"align": "center", "cap_px": 9}),
    ],
    "timg/kaji2_txt2.bflim": [
        ([(0, 0, 194, 38, 0)], 0, "Perfection!", {"align": "center", "cap_px": 16}),
        ([(0, 48, 136, 86, 0)], 0, "Success!", {"align": "center", "cap_px": 16}),
        ([(0, 88, 138, 128, 0)], 0, "Failure!", {"align": "center", "cap_px": 16}),
    ],
    "timg/kaji2_power_txt.bflim": [
        ([(0, 0, 32, 42, 1)], 0, "Temp.", {"align": "center", "cap_px": 14}),
    ],
    "timg/skill_txt_atari.bflim": [
        ([(0, 0, 32, 32, 0)], 0, "H", {"align": "center", "cap_px": 18}),
        ([(32, 0, 64, 32, 0)], 0, "I", {"align": "center", "cap_px": 18}),
        ([(64, 0, 96, 32, 0)], 0, "T", {"align": "center", "cap_px": 18}),
        ([(96, 0, 128, 32, 0)], 0, "!", {"align": "center", "cap_px": 18}),
    ],
}
TEXTURE_LABELS.update({
    "timg/icon_skip.bflim": ("Skip", "label", {"align": "center"}),                 # スキップ
    "timg/icon_skip2.bflim": ("Skip", "label", {"align": "center"}),
    "timg/jackpot_txt.bflim": ("JACKPOT!", "label", {"align": "center", "cap_px": 21}),            # ジャックポット
    "timg/dive_txt_start.bflim": ("Start!!", "label", {"align": "center", "outline": 2}),
    "timg/racing_start_start.bflim": ("Start!!", "label", {"align": "center", "outline": 2}),
    "timg/racing_last.bflim": ("Final Stretch!!", "label", {"align": "center"}),     # ラストスパート
    "timg/racing_best_3.bflim": ("New Best Time!!", "label", {"align": "center", "outline": 2, "cap_px": 24}),
    "timg/location_b002_001a.bflim": ("Downtown Heliodor", "label", {"align": "center"}),
    "timg/skill_txt_atari2.bflim": ("Hit for a Bonus!", "label", {"align": "left"}),
})
DISPLAY = {"cap_px": 17, "glow": True}       # white lettering with a glow
DISPLAY_SMALL = {"cap_px": 11, "glow": True}
PIECE = {"small": True, "margin": 0, "baseline": 10}     # payout table words: one size, one baseline
ATLAS_LABELS.update({
    "timg/skill_txt_atari3.bflim": [([(0, 48, 48, 64, 0)], 0, "")],        # tail of the line above
    # Ⓧまとめてふりわけ: the game's help text calls it Allocate All (Learn Multiple Skills had to
    # be squeezed); capitals centred on the Ⓧ (its rows 9-21)
    "timg/skill_up_alpha_3d.bflim": [([(352, 48, 464, 80, 0)], 22, "Allocate All", {"small": True, "baseline": 19})],
    "timg/racing_gauge_base2.bflim": [([(0, 0, 64, 16, 0)], 17, "Dash", {"small": True, "margin": 2})],   # Ⓨダッシュ
    "timg/dungeon_caption.bflim": [([(0, 0, 64, 16, 0)], 18, "Back", {"small": True})],     # Ⓑもどる
    # the bottom-screen payout table (menu_poker_list_3d): each row is made of word pieces
    # (ロイヤル + ストレート + フラッシュ ...), so the pieces stay single words
    "timg/poker_up_txt3.bflim": [
        ([(0, 0, 64, 16, 0)], 0, "Two Pair", PIECE),                    # ツーペア
        ([(64, 0, 128, 16, 0)], 0, "Jelly", PIECE),                     # スライム (Royal Jelly Flush)
        ([(0, 16, 64, 32, 0)], 0, "Royal", PIECE),                      # ロイヤル
        ([(0, 32, 104, 48, 0)], 0, "Five of a Kind", PIECE),            # ファイブカード (piece widened)
        ([(0, 48, 104, 64, 0)], 0, "Four of a Kind", PIECE),            # フォーカード (widened)
        ([(0, 64, 80, 80, 0)], 0, "Full House", PIECE),                 # フルハウス (widened)
        ([(0, 80, 64, 96, 0)], 0, "Flush", PIECE),                      # フラッシュ
        ([(0, 96, 64, 112, 0)], 0, "Straight", PIECE),                  # ストレート
        ([(0, 112, 112, 128, 0)], 0, "Three of a Kind", PIECE),         # スリーカード (widened)
    ],
    # the roulette's top-screen display (menu_roulette_display_3d): 当たると / 27倍 ... 宝箱, けっか
    "timg/roulette_display.bflim": [
        ([(0, 0, 80, 42, 0)], 0, "Payout", DISPLAY_SMALL),
        ([(80, 0, 160, 42, 0)], 0, "x27", DISPLAY),
        ([(160, 0, 240, 42, 0)], 0, "x14", DISPLAY),
        ([(0, 42, 64, 84, 0)], 0, "x9", DISPLAY),
        ([(64, 42, 128, 84, 0)], 0, "x7", DISPLAY),
        ([(128, 42, 192, 84, 0)], 0, "x5", DISPLAY),
        ([(192, 42, 256, 84, 0)], 0, "x3", DISPLAY),
        ([(0, 84, 64, 128, 0)], 0, "x2", DISPLAY),
        ([(64, 84, 128, 128, 0)], 0, "Chest", DISPLAY_SMALL),
        ([(192, 84, 256, 128, 0)], 0, "Result", DISPLAY_SMALL),
    ],
    "timg/credit_txt1.bflim": [
        ([(0, 38, 256, 62, 0)], 0, "Game Design & Scenario", {"align": "center", "cap_px": 7}),
        ([(0, 62, 256, 94, 0)], 0, "Yuji Horii", {"align": "center", "cap_px": 14}),
    ],
})
# Light text on a dark bar: (rect (x0, y0, x1, y1) around the old text, English, cap_px)
BUTTON_LABELS = {
    "timg/slot_up_base.bflim": [((430, 11, 500, 25), "Set Bet"), ((432, 35, 490, 49), "Start"),
                                ((432, 57, 484, 71), "Quit")],
    "timg/poker_up_base3.bflim": [((30, 11, 100, 25), "Set Bet"), ((32, 35, 90, 49), "Start"),
                                  ((32, 57, 84, 71), "Quit"), ((32, 102, 84, 122), "Deal", "center")],
    "timg/poker_up_plate1.bflim": [((36, 9, 92, 26), "Change", "center")],             # かえる
    "timg/poker_up_plate2.bflim": [((36, 9, 92, 26), "Keep", "center")],             # のこす
    "timg/roulette_lw_txt.bflim": [((42, 11, 110, 24), "Move Cursor"), ((40, 35, 110, 47), "Start"),
                                   ((44, 55, 108, 72), "Set Bet"), ((45, 78, 108, 94), "Bet x10"),
                                   ((42, 103, 100, 115), "Quit")],
}
# The Dash gauge bar has the label's text drawn into it too, exactly under the label pane: the
# bar gets the redrawn label's pixels at that offset, so the two stay identical (no ghosting).
# bar texture -> (label texture, (x, y) of the label on the bar, icon columns, old text rect)
COPIED_LABELS = {"timg/racing_gauge_base.bflim": ("timg/racing_gauge_base2.bflim", (47, 6), 17, (64, 6, 114, 22))}
# glow texture -> (its label texture, columns of the icon ring that stay)
GLOW_LABELS = {"timg/racing_gauge_base3.bflim": ("timg/racing_gauge_base2.bflim", 18)}
# Words drawn one piece per pane, the panes bouncing one after the other (menu_*_3d layouts):
# the English is drawn once as a continuous word, with one colour gradient, across the panes'
# x positions and cut into slices at the middle between neighbouring panes, so it reads as
# one word at rest. texture -> words: (cells (x0, y0, x1, y1), pane centres in layout x,
# English, cap_px[, options]). A cell shown by two panes (the last "!" of あたり!!) is a word
# of its own, so its copy repeats it.
LETTER_WORDS = {
    # コインゲット: as many letters as cells, so one letter per pane, centred (no cuts)
    "timg/coinget_font_3d.bflim": [([(32 * i, 0, 32 * i + 32, 32)], [x], c, 22, {"baseline": 26})
                                   for i, (x, c) in enumerate(zip([-120, -90, -60, -30, 2, 30], "COINS!"))],
    "timg/free_font.bflim": [([(32 * i, 0, 32 * i + 32, 32) for i in range(5)] + [(160, 0, 196, 32)],
                              [-64, -40, -17, 9, 34, 64], "FREE SPIN", 16)],             # フリースピン
    "timg/metal_font.bflim": [([(32 * i, 0, 32 * i + 32, 32) for i in range(5)] + [(160, 0, 196, 32), (196, 0, 228, 32)],
                               [-85, -61, -36, -10, 13, 38, 66], "SILVER SPIN", 16)],    # メタルチェンジ
    "timg/roulette_txt_start.bflim": [                                                  # menu_roulette_txt_3d
        ([(0, 0, 48, 48), (48, 0, 96, 48), (96, 0, 144, 48)], [-56, -13, 25], "WIN", 30,
         {"align": "right", "rings": 6, "draw": 1}),                                               # あたり
        ([(144, 0, 176, 48)], [53], "!", 30, {"rings": 6, "draw": 1}),                             # ! (twice)
        ([(0, 48, 48, 96), (48, 48, 96, 96), (96, 48, 144, 96)], [-43, 0, 44], "MISS", 30, {"rings": 6, "draw": 1}),  # はずれ
        ([(0, 96, 176, 128)], [0], "SPIN!", 22, {"rings": 4, "draw": 1}),                     # スピンスタート!
    ],
    "timg/stamp_txt_3d.bflim": [                                                        # menu_stamp_itemget_3d
        ([(32 * i, 0, 32 * i + 32, 32) for i in range(7)], [-92, -66, -40, -13, 14, 39, 62], "NEW ITEM", 20,
         {"align": "right", "rings": 4, "draw": 1}),                                               # アイテムゲット
        ([(224, 0, 256, 32)], [83], "!", 20, {"rings": 4, "draw": 1}),                             # ! (twice)
    ],
}
# The same across separate textures, one per pane (common_3d_up_racing: ゴール!!, the finish line)
MULTI_LETTERS = [
    (["timg/racing_goal_1.bflim", "timg/racing_goal_2.bflim", "timg/racing_goal_3.bflim"],
     [-207, -172, -133], "FINISH", 23, {"rings": 6, "draw": 1}),
    (["timg/racing_goal_4.bflim"], [-90], "!", 23, {"rings": 6, "draw": 1}),                      # ! (twice)
]
# Race ranks: 1位 -> 1st (the counter's rect, English suffix, cap_px, rings measured, drawn)
SUFFIX_LABELS = {
    "timg/racing_rank1.bflim": ((68, 24, 100, 64), "st", 20, 3, 1),
    "timg/racing_rank2.bflim": ((74, 28, 104, 62), "nd", 20, 3, 1),
    "timg/racing_rank3.bflim": ((70, 30, 104, 64), "rd", 20, 3, 1),
    "timg/racing_rank4.bflim": ((70, 26, 104, 62), "th", 20, 3, 1),
}
PACKS = {"common_2d.pack", "common_2way.pack", "common_3d.pack", "record_3d.pack", "title.pack",
         "dive_dugeon_2way.pack", "panel_3d.pack", "smith_3d.pack", "equip_3d.pack",
         "battle_3d.pack", "casino_slot_3d.pack", "poker_3d.pack", "roulette_3d.pack",
         "horseRace_3d.pack", "credit_3d.pack", "stamp_3d.pack",
         # the map frame ("Close map" / "Swap map") is a copy in every field map pack
         "m002_001a_2d.pack", "w001_001a_2d.pack", "w001_001b_2d.pack", "w001_001c_2d.pack",
         "w001_001d_2d.pack", "w002_001a_3d.pack", "w002_001b_3d.pack", "w002_001c_3d.pack",
         "world_map_a_2d.pack", "world_map_a_3d.pack", "world_map_b_2d.pack", "world_map_b_3d.pack"}


def redraw_atlas(bflim: bytes, labels: list, font: Font, small: Font | None = None) -> bytes:
    px = texture.decode(bflim).copy()
    for item in labels:
        if len(item) == 4:
            pieces, icon, text, opt = item
        else:
            pieces, icon, text = item
            opt = {}
        align = opt.get("align", "left")
        margin = opt.get("margin", 1)
        cap_px = opt.get("cap_px", 9)
        strip = np.concatenate([np.rot90(px[y0:y1, x0:x1], k) for x0, y0, x1, y1, k in pieces], 1)
        strip = np.ascontiguousarray(strip)
        f, level = (small, 0) if opt.get("small") and small else (font, FONT_LEVEL)
        if opt.get("small") and small:
            top, bottom = _cap_rows(small)
            cap_px = bottom - top + 1                 # its own size
        if opt.get("glow"):
            strip[:, icon:] = restyle_glow(strip[:, icon:], text, font, cap_px)
        elif opt.get("rings"):
            strip[:, icon:] = restyle_rings(strip[:, icon:], text, font, opt["rings"], align, cap_px, draw=opt.get("draw"))
        else:
            strip[:, icon:] = restyle(strip[:, icon:], text, f, align, margin=margin, level=level, cap_px=cap_px,
                                      bold=opt.get("bold", 0), spacing=opt.get("spacing", 0),
                                      outline=opt.get("outline", 1), baseline=opt.get("baseline"))
        x = 0
        for x0, y0, x1, y1, k in pieces:
            w = (x1 - x0) if k % 2 == 0 else (y1 - y0)
            px[y0:y1, x0:x1] = np.rot90(strip[:, x:x + w], -k)
            x += w
    return _encode(px, bflim)


# The payout table's rows are made of word pieces side by side (Royal | Straight | Flush): the
# pieces after the first word of a row are moved so the words sit one space apart, measured on
# the redrawn texture (menu_poker_list_3d.bflyt, pane x; panes are left-aligned)
POKER_LIST_LAYOUT = "blyt/menu_poker_list_3d.bflyt"
POKER_LIST_TEXTURE = "timg/poker_up_txt3.bflim"
POKER_PIECES = {"royal": (0, 16, 64, 32), "straight": (0, 96, 64, 112), "flush": (0, 80, 64, 96),
                "slime": (64, 0, 128, 16)}
# official (PC) names: Royal Flush (its Straight piece hidden) and Royal Jelly Flush (the slime
# piece says Jelly, the row's Straight pane shows the Flush piece after it)
POKER_ROWS = [(("txt7", "straight"), ("txt8", "flush")),
              (("txt10", "royal"), ("txt12", "flush")),
              (("txt13", "royal"), ("txt15", "slime"), ("txt14", "flush"))]
POKER_HIDDEN = ("txt11",)
POKER_SHOWS = {"txt14": "flush"}
POKER_SPACE = 4
POKER_LIST_W = {"txt2": 112.0, "txt5": 80.0, "txt6": 104.0, "txt9": 104.0}   # widened pieces


def poker_list_x(px: np.ndarray) -> dict[str, float]:
    """Pane x of the second and third word of each row (the first keeps its x)."""
    ink = {}
    for name, (x0, y0, x1, y1) in POKER_PIECES.items():
        cols = np.where(px[y0:y1, x0:x1, 3].max(0) > 60)[0]
        ink[name] = (int(cols[0]), int(cols[-1]) + 1)
    out = {}
    for row in POKER_ROWS:
        x = -132.0                                  # the first word's pane x (every row)
        for (pane, word), (nxt, nword) in zip(row, row[1:]):
            x = x + ink[word][1] + POKER_SPACE - ink[nword][0]
            out[nxt] = x
    return out


def fix_poker_list(data: bytes, xs: dict[str, float]) -> bytes:
    from .images import _pics
    from .bflyt import Bflyt
    out = bytearray(data)
    for name, off, _ in _pics(data, Bflyt(data)):
        base = name.rsplit("_", 1)[0]
        if not name.endswith(("_normal", "_flash")):
            continue
        if base in xs:
            struct.pack_into("<f", out, off + 0x2C, xs[base])
        if base in POKER_HIDDEN:
            struct.pack_into("<2f", out, off + 0x4C, 0.0, 0.0)
        if base in POKER_SHOWS:                    # another piece's texture coordinates
            x0, y0, x1, y1 = POKER_PIECES[POKER_SHOWS[base]]
            u0, v0, u1, v1 = x0 / 128, y0 / 128, x1 / 128, y1 / 128
            struct.pack_into("<8f", out, off + 0x68, u0, v0, u1, v0, u0, v1, u1, v1)
        if base in POKER_LIST_W:                  # pane width and the right edge of its texture coordinates
            w = POKER_LIST_W[base]
            struct.pack_into("<f", out, off + 0x4C, w)
            u0 = struct.unpack_from("<f", out, off + 0x68)[0]
            for corner in (1, 3):                  # top right, bottom right
                struct.pack_into("<f", out, off + 0x68 + 8 * corner, u0 + w / 128)
    return bytes(out)


# Words built from shared letter cells (the poker results: ロイヤル, ストレート, フラッシュ... share
# their kana), so no cell can hold one piece of an English word. Each line becomes one pane: the
# line's first pane (its drop-in and burst animations stay) shows the whole English line, drawn
# in the line's ring colours into a freshly packed texture, and moves to the line's centre (its
# transX keys move with it); the line's other panes shrink to nothing.
# layout -> (animations whose transX keys move, [(texture, panes, English, cap_px)])
_RS = [f"txt_RSflush{i}" for i in range(1, 10)]
LINE_LAYOUTS = {
    "blyt/menu_poker_txt_3d.bflyt": ("anim/menu_poker_txt_3d_", [
        ("timg/poker_up_txt1.bflim", [f"txt_two{i}" for i in range(1, 6)], "TWO PAIR", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_three{i}" for i in range(1, 8)], "THREE OF A KIND", 15),
        ("timg/poker_up_txt1.bflim", [f"txt_straight{i}" for i in range(1, 7)], "STRAIGHT", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_flush{i}" for i in range(1, 7)], "FLUSH", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_full{i}" for i in range(1, 7)], "FULL HOUSE", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_four{i}" for i in range(1, 8)], "FOUR OF A KIND", 15),
        ("timg/poker_up_txt1.bflim", [f"txt_Sflush{i}" for i in range(7, 12)], "STRAIGHT", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_Sflush{i}" for i in range(1, 7)], "FLUSH", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_five{i}" for i in range(1, 9)], "FIVE OF A KIND", 15),
        ("timg/poker_up_txt1.bflim", _RS, "ROYAL STRAIGHT", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_RSflush{i}" for i in range(10, 16)], "FLUSH", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_RSslime{i}" for i in range(2, 11)], "ROYAL STRAIGHT", 18),
        ("timg/poker_up_txt1.bflim", [f"txt_RSslime{i}" for i in (11, 12, 13, 1, 14)], "SLIME", 18),
        ("timg/poker_up_txt2.bflim", [f"txt_double{i}" for i in range(1, 8)], "DOUBLE UP", 18),     # ダブルアップ!
        ("timg/poker_up_txt2.bflim", [f"txt_lose{i}" for i in range(1, 6)], "TOO BAD...", 18),      # ざんねん…
        ("timg/poker_up_txt2.bflim", [f"txt_draw_{i}" for i in range(1, 5)], "DRAW", 18),          # 引き分け
    ]),
}
OFFICIAL_LINE_H = {"timg/poker_up_txt1.bflim": 30}     # official hand names: 13 lines must fit 256x256
LINE_RINGS = 6          # rings measured on the kana (one drawn: see restyle_rings)


def _flpa_keys(data: bytes, pane: str):
    """(file offset of the value, key size) of every transX key of `pane` in a bflan, and the
    curve: [(frame, value)] (hermite and step curves)."""
    out = []
    n = struct.unpack_from("<H", data, 0x10)[0]
    o = struct.unpack_from("<H", data, 0x06)[0]
    for _ in range(n):
        magic, size = data[o:o + 4], struct.unpack_from("<I", data, o + 4)[0]
        if magic == b"pai1":
            count = struct.unpack_from("<H", data, o + 14)[0]
            table = o + struct.unpack_from("<I", data, o + 16)[0]
            for i in range(count):
                e = o + struct.unpack_from("<I", data, table + 4 * i)[0]
                if data[e:e + 28].split(b"\0")[0].decode() != pane:
                    continue
                for t in range(data[e + 28]):
                    to = e + struct.unpack_from("<I", data, e + 32 + 4 * t)[0]
                    if data[to:to + 4] != b"FLPA":
                        continue
                    for k in range(data[to + 4]):
                        te = to + struct.unpack_from("<I", data, to + 8 + 4 * k)[0]
                        if data[te + 1] != 0:                  # transX
                            continue
                        size_k = 12 if data[te + 2] == 2 else 8
                        ko = te + struct.unpack_from("<I", data, te + 8)[0]
                        for j in range(struct.unpack_from("<H", data, te + 4)[0]):
                            out.append((ko + j * size_k + 4, struct.unpack_from("<2f", data, ko + j * size_k)))
        o += size
    return out


def _rest_x(anims: dict, pane: str, layout_x: float) -> float:
    """Where the pane stands while the word is shown: its transX at frame 25 of its animation
    (letters that slide in), else its layout x."""
    for data in anims.values():
        keys = sorted(fv for _, fv in _flpa_keys(data, pane))
        if keys:
            before = [v for f, v in keys if f <= 25]
            return before[-1] if before else keys[0][1]
    return layout_x


def fix_line_layout(layout: bytes, anims: dict, textures: dict, lines: list, font: Font):
    """(new layout, {anim name: new data}, {texture name: new bflim}) - see LINE_LAYOUTS."""
    from .bflyt import Bflyt
    from .images import _pics
    lay = bytearray(layout)
    pics = {name: off for name, off, _ in _pics(layout, Bflyt(layout))}
    anims = {k: bytearray(v) for k, v in anims.items()}
    px = {t: texture.decode(raw) for t, raw in textures.items()}
    images, placed = {t: [] for t in textures}, []
    for tex, panes, text, cap in lines:
        panes = [p for p in panes if p in pics]
        if tex not in px or not panes:
            continue
        old = px[tex]
        TH, TW = old.shape[:2]
        geo = []
        for p in panes:
            off = pics[p]
            x = struct.unpack_from("<f", lay, off + 0x2C)[0]
            w, h = struct.unpack_from("<2f", lay, off + 0x4C)
            uv = struct.unpack_from("<8f", lay, off + 0x68)
            cell = old[round(uv[1] * TH):round(uv[5] * TH), round(uv[0] * TW):round(uv[2] * TW)]
            geo.append((p, off, _rest_x({k: bytes(v) for k, v in anims.items()}, p, x), cell))
        order = sorted(geo, key=lambda g: g[2])
        h = max(g[3].shape[0] for g in geo)
        lo = min(g[2] - g[3].shape[1] / 2 for g in geo)
        hi = max(g[2] + g[3].shape[1] / 2 for g in geo)
        width = max(254, int(hi - lo) + 4)
        canvas = np.zeros((h, width, 4), np.uint8)    # the kana, centred: their colours
        shift = width / 2 - (lo + hi) / 2
        for _, _, x, cell in order:
            cx = round(x + shift - cell.shape[1] / 2)
            view = canvas[:cell.shape[0], cx:cx + cell.shape[1]]
            take = cell[..., 3] > view[..., 3]
            view[take] = cell[take]
        if isinstance(text, np.ndarray):             # official artwork: centred on the line
            from . import pctex
            src = pctex.trim(text)
            ih = round(cap or h)
            iw = round(src.shape[1] * ih / src.shape[0])
            if iw > TW - 2:
                ih, iw = round(ih * (TW - 2) / iw), TW - 2
            img = pctex.resize(src, iw, ih)
            centre = (lo + hi) / 2
        else:
            while True:                               # smaller until it fits the texture
                word = restyle_rings(canvas, text, font, LINE_RINGS, "center", cap, draw=1)
                cols = np.where(word[..., 3].max(0) > 0)[0]
                if cols[-1] - cols[0] + 1 <= TW - 2:
                    break
                cap -= 0.5
            rows = np.where(word[..., 3].max(1) > 0)[0]
            r0, r1 = min(rows[0], h - 1 - rows[-1]), max(rows[-1], h - 1 - rows[0])     # centred rows
            img = word[r0:r1 + 1, cols[0]:cols[-1] + 1]
            centre = (cols[0] + cols[-1] + 1) / 2 - shift
        key = id(text) if isinstance(text, np.ndarray) else text
        images[tex].append((key, img))
        placed.append((tex, geo, img, centre, (tex, key, img.shape)))
    # pack each texture's lines (same text: one image) in shelves
    where = {}
    for tex, imgs in images.items():
        if not imgs:
            continue
        TH, TW = px[tex].shape[:2]
        new = np.zeros_like(px[tex])
        x = y = row_h = 0
        for text, img in sorted(imgs, key=lambda t: -t[1].shape[0]):
            key = (tex, text, img.shape)
            if key in where:
                continue
            ih, iw = img.shape[:2]
            if x + iw > TW:
                x, y, row_h = 0, y + row_h + 1, 0
            if y + ih > TH:
                raise ValueError(f"texture label: {tex} has no room for {text}")
            new[y:y + ih, x:x + iw] = img
            where[key] = (x, y)
            x, row_h = x + iw + 1, max(row_h, ih)
        px[tex] = new
    for tex, geo, img, centre, key in placed:
        TH, TW = px[tex].shape[:2]
        ih, iw = img.shape[:2]
        x0, y0 = where[key]
        carrier, off, rest, _ = geo[0]
        delta = centre - rest
        struct.pack_into("<f", lay, off + 0x2C, struct.unpack_from("<f", lay, off + 0x2C)[0] + delta)
        struct.pack_into("<2f", lay, off + 0x4C, iw, ih)
        u0, u1, v0, v1 = x0 / TW, (x0 + iw) / TW, y0 / TH, (y0 + ih) / TH
        struct.pack_into("<8f", lay, off + 0x68, u0, v0, u1, v0, u0, v1, u1, v1)
        for data in anims.values():
            for at, (_, v) in _flpa_keys(bytes(data), carrier):
                struct.pack_into("<f", data, at, v + delta)
        for _, o, _, _ in geo[1:]:
            struct.pack_into("<2f", lay, o + 0x4C, 0.0, 0.0)
    return (bytes(lay), {k: bytes(v) for k, v in anims.items()},
            {t: _encode(px[t], textures[t]) for t in textures})


# A word of part panes, one letter layout each, where one letter layout is used twice
# (ジャックポット: ッ is jackpot3 for txt3 and txt6): the twin moves onto its original, where it
# shows the same picture, and the other parts spread evenly over the word's width, so English
# with no repeated letter (JACKPOT) is drawn across the unique cells.
# layout -> (texture, cells, parts in cell order, (twin, its original), English, cap_px, options)
SPREAD_WORDS = {
    "blyt/menu_casino_jackpot_3d.bflyt": (
        "timg/jackpot_txt2.bflim", [(42 * i, 0, 42 * i + 42, 48) for i in range(6)],
        ["txt1", "txt2", "txt3", "txt4", "txt5", "txt7"], ("txt6", "txt3"), "JACKPOT", 26,
        {"rings": 3, "draw": 1, "bright": True}),
}


def _parts(data: bytes) -> dict:
    """name -> section offset of every part pane (prt1)."""
    out = {}
    o = struct.unpack_from("<H", data, 0x06)[0]
    for _ in range(struct.unpack_from("<H", data, 0x10)[0]):
        magic, size = data[o:o + 4], struct.unpack_from("<I", data, o + 4)[0]
        if magic == b"prt1":
            out[data[o + 0x0C:o + 0x24].split(b"\0")[0].decode()] = o
        o += size
    return out


def fix_spread_word(layout: bytes, bflim: bytes, spec: tuple, font: Font) -> tuple[bytes, bytes]:
    _, cells, parts, (twin, of), text, cap, opt = spec
    lay = bytearray(layout)
    offs = _parts(layout)
    first = struct.unpack_from("<f", lay, offs[parts[0]] + 0x2C)[0]
    last = struct.unpack_from("<f", lay, offs[parts[-1]] + 0x2C)[0]
    xs = [first + (last - first) * i / (len(parts) - 1) for i in range(len(parts))]
    for p, x in zip(parts, xs):
        struct.pack_into("<f", lay, offs[p] + 0x2C, x)
    struct.pack_into("<f", lay, offs[twin] + 0x2C, xs[parts.index(of)])
    px = texture.decode(bflim).copy()
    new = word_slices([px[y0:y1, x0:x1] for x0, y0, x1, y1 in cells], xs, text, cap, font, **opt)
    for (x0, y0, x1, y1), cell in zip(cells, new):
        px[y0:y1, x0:x1] = cell
    return bytes(lay), _encode(px, bflim)


def redraw_buttons(bflim: bytes, items: list, font: Font, small: Font | None = None) -> bytes:
    px = texture.decode(bflim)
    for rect, text, *opt in items:
        align = opt[0] if opt else "left"
        if small:
            px = restyle_button(px, rect, text, small, cap_px=None, align=align, level=0, bold=0)
        else:
            px = restyle_button(px, rect, text, font, align=align)
    return _encode(px, bflim)


def place_image(canvas: np.ndarray, img: np.ndarray, align: str = "center", height: float | None = None,
                max_w: int | None = None) -> np.ndarray:
    """`img` (official artwork, see pctex) on a transparent copy of `canvas`, `height` pixels tall
    (default: as tall as the old lettering), centred on the old lettering's rows and placed by
    `align` over its columns; narrower than max_w (default: the canvas)."""
    from . import pctex
    H, W = canvas.shape[:2]
    ink = canvas[..., 3] > ALPHA_INK
    ys, xs = np.where(ink) if ink.any() else (np.array([0, H - 1]), np.array([0, W - 1]))
    src = pctex.trim(img)
    h = height or (ys.max() - ys.min() + 1)
    if h <= 1:                                   # a fraction of the old lettering's height
        h *= ys.max() - ys.min() + 1
    w = src.shape[1] * h / src.shape[0]
    limit = max_w or W - 2
    if w > limit:
        h, w = h * limit / w, limit
    h, w = max(1, min(H, round(h))), max(1, round(w))
    small = pctex.resize(src, w, h)
    y = max(0, min(H - h, round((ys.min() + ys.max() + 1 - h) / 2)))
    if align == "right":
        x = xs.max() + 1 - w
    elif align == "left":
        x = xs.min()
    else:
        x = round((W - w) / 2)
    x = max(0, min(W - w, x))
    out = np.zeros_like(canvas)
    out[y:y + h, x:x + w] = small
    return out


def _word_cuts(word: np.ndarray, cells: list, centres: list, lefts: list, pad: int) -> tuple:
    """Where word_slices cuts `word` between neighbouring panes, and the ink those cuts go
    through: where both panes cover the word, at the column with the least ink (a gap between
    letters) - a cut through a letter shows as a line once the panes are scaled and filtered, and
    as a sliced letter while the panes animate one by one; nearest the middle when there is a
    choice. Panes that only touch are cut at their edge."""
    ink = word[..., 3].astype(np.float32).sum(0)
    cuts, cost = [], 0.0
    for i in range(len(cells) - 1):
        mid = round((centres[i] + centres[i + 1]) / 2 - centres[0]) + pad
        a, b = lefts[i + 1] + 1, lefts[i] + cells[i].shape[1] - 1       # both panes cover [a, b)
        if a >= b:
            cut = min(max(mid, lefts[i + 1]), lefts[i] + cells[i].shape[1])
        else:
            cols = np.arange(a, b)
            cut = int(cols[np.lexsort((np.abs(cols - mid), ink[a:b]))[0]])
        cuts.append(cut)
        cost += ink[max(0, cut - 1):cut + 1].sum()
    return cuts, cost


def _place_between(canvas: np.ndarray, img: np.ndarray, align: str, height, cells: list,
                   centres: list, lefts: list, pad: int) -> np.ndarray:
    """place_image, its words (separated by empty columns) each nudged sideways - and if that is
    not enough, made a little smaller - so the cuts between the panes fall between words, or else
    where letters only touch: panes that only touch cannot move their cut."""
    import itertools
    W = canvas.shape[1]
    base = place_image(canvas, img, align, height)
    ys, xs = np.where(base[..., 3] > 0)
    if not len(xs):
        return base
    full = (ys.max() - ys.min() + 1) if not height or height <= 1 else height
    best = None
    for k, scale in enumerate((1.0, 0.96, 0.92, 0.88)):
        word = base if scale == 1 else place_image(canvas, img, align, max(1, round(full * scale)))
        used = np.append(word[..., 3].any(0), False)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], used.astype(np.int8)))))
        groups = list(zip(edges[::2], edges[1::2]))[:3]
        groups[-1] = (groups[-1][0], int(np.flatnonzero(used).max()) + 1)
        for dxs in itertools.product(range(-8, 9), repeat=len(groups)):
            spans = [(g0 + d, g1 + d) for (g0, g1), d in zip(groups, dxs)]
            if spans[0][0] < 0 or spans[-1][1] > W or any(
                    spans[i][1] + 2 > spans[i + 1][0] for i in range(len(spans) - 1)):
                continue
            moved = np.zeros_like(word)
            for (g0, g1), d in zip(groups, dxs):
                moved[:, g0 + d:g1 + d] = word[:, g0:g1]
            cost = _word_cuts(moved, cells, centres, lefts, pad)[1]
            key = (cost > 255 * 2, cost if cost > 255 * 2 else 0, k, sum(map(abs, dxs)))
            if best is None or key < best[0]:
                best = (key, moved)
        if not best[0][0]:
            break
    return best[1]


def word_slices(cells: list, centres: list, text: str, cap_px: float, font: Font,
                align: str = "center", outline: int = 1, rings: int | None = None,
                draw: int | None = None, bright: bool = False, baseline: int | None = None) -> list:
    """The cells (images shown by centred panes at x `centres`, left to right) with `text`
    drawn once across them and cut at the middle between neighbouring panes.
    rings: decorated lettering (restyle_rings) with that many rings around the core."""
    h = cells[0].shape[0]
    pad = max(c.shape[1] for c in cells) // 2
    c0 = centres[0]
    lefts = [round(c - c0) + pad - cell.shape[1] // 2 for cell, c in zip(cells, centres)]    # canvas x
    width = max(x + cell.shape[1] for cell, x in zip(cells, lefts))       # ends with the last cell
    canvas = np.zeros((h, width, 4), np.uint8)              # the old letters where the panes are
    for cell, x in zip(cells, lefts):
        view = canvas[:, x:x + cell.shape[1]]
        take = cell[..., 3] > view[..., 3]
        view[take] = cell[take]
    if isinstance(text, np.ndarray):            # official artwork (pctex): placed, not drawn
        word = _place_between(canvas, text, align, cap_px, cells, centres, lefts, pad)
    elif rings:
        word = restyle_rings(canvas, text, font, rings, align, cap_px, draw=draw, bright=bright)
    else:
        word = restyle(canvas, text, font, align, margin=0, level=FONT_LEVEL, cap_px=cap_px, outline=outline,
                       baseline=baseline)
    # cuts between neighbouring panes: where both panes cover the word, at the column with the
    # least ink (a gap between letters) - a cut through a letter shows as a line once the panes
    # are scaled and filtered; nearest the middle when there is a choice
    cuts = _word_cuts(word, cells, centres, lefts, pad)[0]
    out = []
    for i, (cell, c, x) in enumerate(zip(cells, centres, lefts)):
        lo = cuts[i - 1] if i else x
        hi = cuts[i] if i < len(cells) - 1 else x + cell.shape[1]
        lo, hi = max(lo, x), min(hi, x + cell.shape[1])
        new = np.zeros_like(cell)
        new[:, lo - x:hi - x] = word[:, lo:hi]
        out.append(new)
    return out


def redraw_letters(bflim: bytes, words: list, font: Font) -> bytes:
    """words: (cells (x0, y0, x1, y1), pane centres, English, cap_px[, options])"""
    px = texture.decode(bflim).copy()
    for cells, centres, text, cap, *opt in words:
        opt = opt[0] if opt else {}
        new = word_slices([px[y0:y1, x0:x1] for x0, y0, x1, y1 in cells], centres, text, cap, font, **opt)
        for (x0, y0, x1, y1), cell in zip(cells, new):
            px[y0:y1, x0:x1] = cell
    return _encode(px, bflim)


def redraw_suffix(rgba: np.ndarray, rect: tuple, text: str, font: Font, cap_px: float, rings: int,
                  draw: int) -> np.ndarray:
    """A big numeral's small Japanese counter (1位) replaced by an English suffix (1st): inside
    `rect`, what is further than `rings` from the numeral's fill (left of the rect) is erased,
    and the suffix is drawn in the counter's rings behind the numeral."""
    x0, y0, x1, y1 = rect
    sil = rgba[..., 3] > ALPHA_INK
    core = _depth(sil) > rings
    core[:, x0:] = False
    keep = _grow(core, rings + 1)
    base = rgba.copy()
    region = np.zeros_like(sil)
    region[y0:y1, x0:x1] = True
    base[region & ~keep] = 0
    # in the numeral's own rings, standing on its baseline
    bottom = np.where(core.any(1))[0][-1]
    suffix = restyle_rings(rgba, text, font, rings, "left", cap_px, draw=draw, at=(x0 + draw + 1, bottom))
    k = base[..., 3:].astype(np.float32) / 255
    out = base.astype(np.float32)
    out[..., :3] = base[..., :3] * k + suffix[..., :3] * (1 - k)
    out[..., 3:] = np.maximum(base[..., 3:], suffix[..., 3:])
    return out.round().astype(np.uint8)


def redraw(bflim: bytes, text: str, kind: str, font: Font, **opt) -> bytes:
    px = texture.decode(bflim)
    if kind == "pill":
        new = restyle_pill(px, text, font, level=FONT_LEVEL, **opt)
    elif kind == "badge":
        new = restyle_badge(px, text, font, **opt)
    else:
        new = restyle(px, text, font, level=FONT_LEVEL, **opt)
    return _encode(new, bflim)


def _encode(px: np.ndarray, bflim: bytes) -> bytes:
    if texture.FORMATS.get(texture.info(bflim)["format"], ("",))[0] in ("RGBA4", "RGBA8", "LA8", "LA4", "A8",
                                                                      "RGB565", "RGBA5551"):
        return texture.encode(px, bflim)
    return texture.encode_rgba4(px, bflim)       # ETC1 / ETC1A4


def _pane_offsets(data: bytes) -> dict:
    """name -> section offset of every pane (pic1, prt1, ...: translate x at +0x2C)."""
    out = {}
    o = struct.unpack_from("<H", data, 0x06)[0]
    for _ in range(struct.unpack_from("<H", data, 0x10)[0]):
        magic, size = data[o:o + 4], struct.unpack_from("<I", data, o + 4)[0]
        if magic in (b"pan1", b"pic1", b"txt1", b"prt1", b"wnd1", b"bnd1"):
            out.setdefault(data[o + 0x0C:o + 0x24].split(b"\0")[0].decode(), o)
        o += size
    return out


def apply_official(sarc, repl: dict, official: dict, font: Font) -> set:
    """Put the PC version's artwork (pctex.official) into this archive; returns the textures
    and layouts it replaced (the drawn labels leave them alone)."""
    done = set()

    def px_of(name):
        return texture.decode(repl.get(name) or sarc.get(name)).copy()
    for tex, items in official["whole"].items():
        if sarc.get(tex) is None:
            continue
        px = px_of(tex)
        for (x0, y0, x1, y1), img, height, *align in items:
            px[y0:y1, x0:x1] = place_image(px[y0:y1, x0:x1], img, align[0] if align else "center", height)
        repl[tex] = _encode(px, sarc.get(tex))
        done.add(tex)
    for tex, img in official["letters"].items():
        if sarc.get(tex) is None:
            continue
        cells, centres = LETTER_WORDS[tex][0][:2]
        repl[tex] = redraw_letters(repl.get(tex) or sarc.get(tex), [(cells, centres, img, None)], font)
        done.add(tex)
    for lay_name, words in official["spread"].items():
        if sarc.get(lay_name) is None or not all(sarc.get(t) for w in words for t, _ in w[0]):
            continue
        lay = bytearray(repl.get(lay_name) or sarc.get(lay_name))
        offs = _pane_offsets(bytes(lay))
        for cells, panes, twin, img, height in words:
            xs0 = [struct.unpack_from("<f", lay, offs[p] + 0x2C)[0] for p in panes + ([twin[0]] if twin else [])]
            lo, hi = min(xs0), max(xs0)
            xs = [lo + (hi - lo) * i / (len(panes) - 1) for i in range(len(panes))] if len(panes) > 1 else [xs0[0]]
            widths = [x1 - x0 for _, (x0, _, x1, _) in cells]
            if any(b - a > (wa + wb) / 2 for a, b, wa, wb in zip(xs, xs[1:], widths, widths[1:])):
                xs = xs0[:len(panes)]                 # spread apart they would leave a gap: stay
            for p, x in zip(panes, xs):
                struct.pack_into("<f", lay, offs[p] + 0x2C, x)
            if twin:
                struct.pack_into("<f", lay, offs[twin[0]] + 0x2C, xs[panes.index(twin[1])])
            texs = {t: px_of(t) for t, _ in cells}
            new = word_slices([texs[t][y0:y1, x0:x1] for t, (x0, y0, x1, y1) in cells], xs, img, height, font)
            for (t, (x0, y0, x1, y1)), cell in zip(cells, new):
                texs[t][y0:y1, x0:x1] = cell
            for t, px in texs.items():
                repl[t] = _encode(px, sarc.get(t))
                done.add(t)
        repl[lay_name] = bytes(lay)
        done.add(lay_name)
    for lay_name, images in official["lines"].items():
        if sarc.get(lay_name) is None:
            continue
        prefix, lines = LINE_LAYOUTS[lay_name]
        lines = [(t, panes, img, OFFICIAL_LINE_H.get(t)) for (t, panes, _, _), img in zip(lines, images)]
        anims = {m.name: sarc.get(m.name) for m in sarc.nodes if m.name.startswith(prefix)}
        texs = {t: sarc.get(t) for t, *_ in lines if sarc.get(t)}
        lay, new_anims, new_texs = fix_line_layout(sarc.get(lay_name), anims, texs, lines, font)
        repl[lay_name] = lay
        repl.update(new_anims)
        repl.update(new_texs)
        done |= {lay_name, *new_texs}
    return done


def patch_pack(pack_data: bytes, font_data: bytes, small_font_data: bytes | None = None,
               official: dict | None = None, edits: dict | None = None) -> bytes | None:
    """The pack with its TEXTURE_LABELS in English, or None if it has none.
    small_font_data: SMALL_FONT, for the small labels (else they use font_data, scaled).
    official: pctex.official() - the PC version's artwork, used instead of drawing a label.
    edits: texture -> RGBA image (hand-edited, same size): used as is, before anything else."""
    from .darc import Sarc
    from .pack import Pack
    font = Font(font_data)
    small = Font(small_font_data) if small_font_data else None
    pack = Pack.parse(pack_data)
    changed = False
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        repl = {}
        done = set()
        for n in sarc.nodes:
            if edits and n.name in edits:
                want = texture.decode(sarc.get(n.name)).shape
                if edits[n.name].shape != want:
                    raise ValueError(f"texture edit {n.name}: must be {want[1]}x{want[0]} pixels, "
                                     f"is {edits[n.name].shape[1]}x{edits[n.name].shape[0]}")
                repl[n.name] = _encode(edits[n.name], sarc.get(n.name))
                done.add(n.name)
        if official:
            done |= apply_official(sarc, repl, official, font) - set(edits or ())
        for n in sarc.nodes:
            if n.name in done:
                continue
            if n.name in ATLAS_LABELS:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw_atlas(raw, ATLAS_LABELS[n.name], font, small)
            elif n.name in TEXTURE_LABELS:
                text, kind, opt = TEXTURE_LABELS[n.name]
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw(raw, text, kind, font, **opt)
            elif n.name in BUTTON_LABELS:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw_buttons(raw, BUTTON_LABELS[n.name], font, small)
            elif n.name in LETTER_WORDS:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = redraw_letters(raw, LETTER_WORDS[n.name], font)
            elif n.name in SUFFIX_LABELS:
                rect, text, cap, rings, draw = SUFFIX_LABELS[n.name]
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = _encode(redraw_suffix(texture.decode(raw), rect, text, font, cap, rings, draw), raw)
        for names, centres, text, cap, opt in MULTI_LETTERS:
            raws = [sarc.get(name) for name in names]
            if any(r is None for r in raws) or done & set(names):
                continue
            new = word_slices([texture.decode(r) for r in raws], centres, text, cap, font, **opt)
            for name, raw, px in zip(names, raws, new):
                repl[name] = _encode(px, raw)
        for n in sarc.nodes:
            if n.name in done:
                continue
            if n.name in SPREAD_WORDS and sarc.get(SPREAD_WORDS[n.name][0]) and SPREAD_WORDS[n.name][0] not in done:
                tex = SPREAD_WORDS[n.name][0]
                repl[n.name], repl[tex] = fix_spread_word(sarc.get(n.name), sarc.get(tex), SPREAD_WORDS[n.name], font)
            if n.name in LINE_LAYOUTS:
                prefix, lines = LINE_LAYOUTS[n.name]
                anims = {m.name: sarc.get(m.name) for m in sarc.nodes if m.name.startswith(prefix)}
                texs = {t: repl.get(t) or sarc.get(t) for t, *_ in lines if sarc.get(t)}
                lay, new_anims, new_texs = fix_line_layout(sarc.get(n.name), anims, texs, lines, font)
                repl[n.name] = lay
                repl.update(new_anims)
                repl.update(new_texs)
            if n.name == POKER_LIST_LAYOUT and POKER_LIST_TEXTURE in repl:
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = fix_poker_list(raw, poker_list_x(texture.decode(repl[POKER_LIST_TEXTURE])))
            if n.name in COPIED_LABELS and COPIED_LABELS[n.name][0] in repl and n.name not in done:
                source, (lx, ly), icon, rect = COPIED_LABELS[n.name]
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                bar = restyle_button(texture.decode(raw), rect, "", font)       # old text painted over
                label = texture.decode(repl[source])[:, icon:].astype(np.float32)
                h, w = label.shape[:2]
                dst = bar[ly:ly + h, lx + icon:lx + icon + w].astype(np.float32)
                k = label[..., 3:] / 255
                dst[..., :3] = label[..., :3] * k + dst[..., :3] * (1 - k)
                dst[..., 3:] = np.maximum(dst[..., 3:], label[..., 3:])
                bar[ly:ly + h, lx + icon:lx + icon + w] = dst.round().astype(np.uint8)
                repl[n.name] = _encode(bar, raw)
            if n.name in GLOW_LABELS and GLOW_LABELS[n.name][0] in repl and n.name not in done:
                source, icon = GLOW_LABELS[n.name]
                raw = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                repl[n.name] = _encode(redraw_glow(texture.decode(raw), texture.decode(repl[source]), icon,
                                                          texture.decode(sarc.get(source))), raw)
        if repl:
            e.data = sarc.rebuild(repl)
            changed = True
    return pack.build() if changed else None
