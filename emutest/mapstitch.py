"""mapstitch.py "R 2" "U 3" ... - walk (circle pad, game seconds per step, in short legs) and
stitch the scrolling bottom-screen map into runs/ctl/map.png (phase correlation between
consecutive frames). The hero stays centred on the 2D map, so the stitched map is a picture
of everything walked past; the red dot marks the start, blue the end."""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from azctl import OUT, Session  # noqa: E402

LEG = 0.35            # game seconds per leg (keeps the shift between frames small)


def bottom(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im)[240:480, 40:360].astype(np.float32)


def _tukey(n, alpha=0.3):
    w = np.ones(n)
    k = int(alpha * n / 2)
    if k:
        r = 0.5 * (1 - np.cos(np.pi * np.arange(k) / k))
        w[:k], w[-k:] = r, r[::-1]
    return w


def shift(a, b):
    """(dy, dx) such that b ~ a shifted by (dy, dx). The centre (hero and followers, which
    stay put while the map scrolls) is masked out."""
    ga, gb = a.mean(2), b.mean(2)
    h, w = ga.shape
    for g, src in ((ga, a), (gb, b)):
        g -= g.mean()
        g[h // 2 - 45:h // 2 + 45, w // 2 - 40:w // 2 + 40] = 0
        r = src[..., 0]; gg = src[..., 1]; bb = src[..., 2]
        ys, xs = np.nonzero((r > 190) & (gg < 110) & (bb < 80))
        if len(xs) >= 8:                      # the party, wherever it is on screen
            cy, cx = int(np.median(ys)), int(np.median(xs))
            g[max(0, cy - 40):cy + 40, max(0, cx - 40):cx + 40] = 0
    win = np.outer(_tukey(h), _tukey(w))
    F = np.fft.fft2(ga * win) * np.conj(np.fft.fft2(gb * win))
    r = np.fft.ifft2(F / (np.abs(F) + 1e-6)).real
    dy, dx = np.unravel_index(np.argmax(r), r.shape)
    if dy > h // 2: dy -= h
    if dx > w // 2: dx -= w
    return int(dy), int(dx)


def main(steps):
    s = Session()
    frames, pos = [], [(0, 0)]
    p = os.path.join(OUT, "st0.png"); s.shot(p); frames.append(bottom(p))
    k = 0
    for st in steps:
        d, secs = st.split()
        for _ in range(max(1, int(float(secs) / LEG))):
            s.walk(d, LEG)
            k += 1
            p = os.path.join(OUT, f"st{k % 2 + 1}.png"); s.shot(p)
            f = bottom(p)
            dy, dx = shift(f, frames[-1])
            pos.append((pos[-1][0] + dy, pos[-1][1] + dx))
            frames.append(f)
    ys = [y for y, _ in pos]; xs = [x for _, x in pos]
    H, W = frames[0].shape[:2]
    canvas = Image.new("RGB", (max(xs) - min(xs) + W, max(ys) - min(ys) + H), "black")
    for f, (y, x) in zip(frames, pos):
        canvas.paste(Image.fromarray(f.astype(np.uint8)), (x - min(xs), y - min(ys)))
    dr = ImageDraw.Draw(canvas)
    for (y, x), c in ((pos[0], "red"), (pos[-1], "blue")):
        cx, cy = x - min(xs) + W // 2, y - min(ys) + H // 2
        dr.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), outline=c, width=2)
    canvas.save(os.path.join(OUT, "map.png"))
    print("map", canvas.size, "frames", len(frames), "t=%.1f" % s.game_time())


if __name__ == "__main__":
    main(sys.argv[1:])
