"""nav.py X Y - walk the hero to a point picked on a screenshot's bottom screen (full screenshot
coordinates, 400x480 layout: the bottom screen is x 40..360, y 240..480; the hero is drawn at
about (200, 360)). Closed loop: the stick->map mapping is calibrated with two short legs (the
movement is camera-relative), then the hero walks in short legs, re-measuring the remaining
offset by phase correlation of the scrolling 2D map, until within a few pixels or blocked.
Prints the final screenshot path."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from azctl import OUT, Session          # noqa: E402
from mapstitch import bottom, shift     # noqa: E402

HERO = (200, 360)
LEG = 0.5


def frame(s, name="nv.png"):
    p = os.path.join(OUT, name)
    s.shot(p)
    return bottom(p)


def leg(s, vx, vy, secs=LEG):
    s.stick(int(vx), int(vy), secs)
    s.wait_game(s.game_time() + 0.15)


def hood(f):
    """Screen position (x, y) of Gemma's red hood on the bottom map (she follows right behind
    the hero), or None."""
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    ys, xs = np.nonzero((r > 190) & (g < 110) & (b < 80))
    if len(xs) < 8:
        return None
    return np.array([np.median(xs), np.median(ys)])


def moved(a, b):
    """Hero displacement in map pixels between frames a and b: the map scroll (content moves
    the other way) plus the sprite's own movement on screen (the map stops scrolling at its
    edges)."""
    dy, dx = shift(b, a)
    d = np.array([-dx, -dy], float)
    ha, hb = hood(a), hood(b)
    if ha is not None and hb is not None:
        d += hb - ha
    return d


def calibrate(s):
    """Least-squares stick->map mapping from short legs in four directions (blocked legs,
    with no measurable movement, are ignored). Returns (A, frame, net hero displacement)."""
    S, D = [], []
    f0 = frame(s)
    net = np.zeros(2)
    for vx, vy in ((156, 0), (0, 156), (-156, 0), (0, -156), (110, 110), (-110, -110)):
        leg(s, vx, vy, 0.6)
        f1 = frame(s)
        d = moved(f0, f1)
        net += d
        f0 = f1
        if np.hypot(*d) >= 2:
            S.append((vx, vy)); D.append(d)
    if len(S) < 2:
        return None, f0, net
    Sm, Dm = np.array(S, float), np.array(D, float)
    At, *_ = np.linalg.lstsq(Sm, Dm, rcond=None)      # D = S @ A^T
    return At.T, f0, net


def goto(s, tx, ty, tol=6, max_legs=80):
    h = hood(frame(s))
    here = (h[0] + 40, h[1] + 240) if h is not None else HERO     # party position on screen
    target = np.array([tx - here[0], ty - here[1]], float)
    A, f, net = calibrate(s)
    if A is None or abs(np.linalg.det(A)) < 1e-6:
        print("calibration failed (blocked?)")
        return False
    Ainv = np.linalg.inv(A)
    remaining = target - net
    stuck = 0
    for _ in range(max_legs):
        dist = np.hypot(*remaining)
        if dist <= tol:
            return True
        v = Ainv @ remaining
        n = np.hypot(*v)
        v = v / n * 156 if n > 0 else v
        secs = min(LEG, LEG * dist / 20) if dist < 20 else LEG
        leg(s, v[0], v[1], max(0.3, secs))
        f1 = frame(s)
        d = moved(f, f1)
        f = f1
        remaining -= d
        if np.hypot(*d) < 1.0:
            stuck += 1
            if stuck >= 3:
                print("blocked, remaining", remaining.round(1).tolist())
                return False
        else:
            stuck = 0
    print("gave up, remaining", remaining.round(1).tolist())
    return False


if __name__ == "__main__":
    s = Session()
    ok = goto(s, float(sys.argv[1]), float(sys.argv[2]))
    p = os.path.join(OUT, "nav.png")
    s.shot(p)
    print("arrived" if ok else "not arrived", p, "t=%.1f" % s.game_time())
