"""doorvision.py - after jevplay's village route: walk into the shop guided by Muse on the 3D view.

Movement is camera-relative, and on the top screen "up" is the camera's forward direction:
Muse marks the doorway on the top screen, the stick is pushed from the hero toward it for a
short step, repeat until the position jumps (map change). Run with a session at the door area
(azctl start --speed 100; jevplay's route) - or standalone: it does the route itself.
"""
import json
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az        # noqa: E402
import azctl     # noqa: E402
import vision    # noqa: E402
import walkto    # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "doorvision")


def doorway(path):
    txt = vision.ask(path, "3DS screenshot (400x480). TOP half (y 0..240) is the 3D view behind the hero "
                           "(the boy in a blue tunic). Find the OPEN DOORWAY (dark entrance) of the shop "
                           "building (it has a sign with a bag) in the top half. Answer only JSON: "
                           '{"hero_x": int, "hero_y": int, "door_x": int, "door_y": int, "visible": true/false}')
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if d.get("visible") else None


def run(route=True, tries=12):
    os.makedirs(OUT, exist_ok=True)
    log = open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8")

    def say(*a):
        line = " ".join(str(x) for x in a)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    if route:
        import jevplay
        from jevnav import Nav
        sc = jevplay.SHOP
        azctl.main(["start", "--save", sc["save"], "--speed", "100", "--boot", "1"])
        s = azctl.Session()
        for d, secs in sc["moves"]:
            s.walk(d, secs)
        s.wait_game(s.game_time() + 1)
        nav = Nav(s, say)
        nav.no_jev = True                     # quota; code picks the best unblocked direction
        for wp in sc["waypoints"]:
            say("waypoint", wp, nav.route(wp, "waypoint"), walkto.pos(s).round(2))
    else:
        s = azctl.Session()
    for i in range(tries):
        p = os.path.join(OUT, f"d{i:02d}.png")
        s.shot(p)
        d = doorway(p)
        say("muse", i, d)
        if not d:
            s.stick(156, 0, 0.15)             # turn a little and look again
            s.wait_game(s.game_time() + 0.3)
            continue
        vx, vy = d["door_x"] - d["hero_x"], d["hero_y"] - d["door_y"]   # screen up = stick up
        n = math.hypot(vx, vy) or 1
        for _ in range(3):
            a = walkto.pos(s)
            s.stick(int(156 * vx / n), int(156 * vy / n), 0.25)
            s.wait_game(s.game_time() + 0.2)
            b = walkto.pos(s)
            if np.linalg.norm(b - a) > 5:
                s.wait_game(s.game_time() + 1.5)
                s.shot(os.path.join(OUT, "inside.png"))
                say("ENTERED", b.round(2))
                return True
    say("not entered", walkto.pos(s).round(2))
    return False


if __name__ == "__main__":
    run(route="--here" not in sys.argv)
