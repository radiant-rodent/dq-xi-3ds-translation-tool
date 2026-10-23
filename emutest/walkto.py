"""walkto.py MAPX MAPY - walk the hero to a point picked on a full screenshot's bottom-screen
map (400x480 layout; the hero is drawn at about (200, 360)). Closed loop on the hero's world
position (POS, a per-frame copy in .bss), not on screenshots:

  - the bottom map is north-up and scrolls with the hero: map pixels -> world x/z with a
    scale calibrated once from a short walk (SCALE_FILE)
  - the stick is mapped to world directions by two short test moves (the camera can turn)
  - short steps (best at --speed 100); when blocked, side steps left/right of the goal

Use `azctl.py start --speed 100` for walking: at unlimited speed every step overshoots.
"""
import json
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from azctl import OUT, Session          # noqa: E402
from mapstitch import bottom, shift     # noqa: E402

POS = 0x7F3110          # hero x, y, z (float); rewritten from the real position every frame
HERO = (200, 360)
SCALE_FILE = os.path.join(OUT, "map_scale.json")
STEP = 0.25             # game seconds per step


def pos(s):
    x, y, z = struct.unpack("<3f", s.read(POS, 12))
    return np.array([x, z])


def stick_basis(s):
    """World (x, z) motion of stick right and stick up (unit vectors)."""
    out = []
    for vx, vy in ((156, 0), (0, 156)):
        for sign in (1, -1):
            a = pos(s)
            s.stick(sign * vx, sign * vy, 0.25)
            s.wait_game(s.game_time() + 0.15)
            d = (pos(s) - a) * sign
            if np.linalg.norm(d) > 0.2:
                out.append(d / np.linalg.norm(d))
                break
        else:
            raise RuntimeError("hero does not move (menu open? blocked on both sides?)")
    return np.array(out)        # rows: right, up


def map_scale(s):
    """Map pixels per world unit (calibrated once, cached)."""
    if os.path.exists(SCALE_FILE):
        return json.load(open(SCALE_FILE))["px_per_unit"]
    p0 = os.path.join(OUT, "cal0.png")
    p1 = os.path.join(OUT, "cal1.png")
    s.shot(p0)
    a = pos(s)
    for vx, vy in ((-156, 0), (156, 0), (0, 156), (0, -156)):
        s.stick(vx, vy, 0.5)
        s.wait_game(s.game_time() + 0.3)
        if np.linalg.norm(pos(s) - a) > 1.0:
            break
    s.shot(p1)
    dy, dx = shift(bottom(p0), bottom(p1))
    scale = math.hypot(dx, dy) / float(np.linalg.norm(pos(s) - a))
    json.dump({"px_per_unit": scale}, open(SCALE_FILE, "w"))
    return scale


def goto_world(s, target, tol=0.4, max_steps=80):
    basis = stick_basis(s)
    inv = np.linalg.inv(basis.T)            # world delta -> (right, up) stick amounts
    last, stuck = pos(s), 0
    for _ in range(max_steps):
        cur = pos(s)
        d = target - cur
        dist = float(np.linalg.norm(d))
        if dist < tol:
            return True
        sx, sy = inv @ (d / dist)
        if stuck:                           # blocked: try 60 degrees to either side
            ang = math.radians(60 if stuck % 2 else -60)
            sx, sy = (sx * math.cos(ang) - sy * math.sin(ang), sx * math.sin(ang) + sy * math.cos(ang))
        n = math.hypot(sx, sy) or 1
        secs = STEP if dist > 1.5 else STEP / 2
        s.stick(int(156 * sx / n), int(156 * sy / n), secs)
        s.wait_game(s.game_time() + 0.1)
        now = pos(s)
        stuck = stuck + 1 if np.linalg.norm(now - last) < 0.1 else 0
        if stuck > 6:
            return False
        last = now
    return False


def goto_map(s, mx, my, **kw):
    """Map point of a screenshot taken now (north up: +x right, +z down)."""
    scale = map_scale(s)
    target = pos(s) + np.array([(mx - HERO[0]) / scale, (my - HERO[1]) / scale])
    return goto_world(s, target, **kw)


if __name__ == "__main__":
    s = Session()
    ok = goto_map(s, float(sys.argv[1]), float(sys.argv[2]))
    p = os.path.join(OUT, "walkto.png")
    s.shot(p)
    print("arrived" if ok else "not arrived", pos(s), p, f"t={s.game_time():.1f}")
