"""shopwalk.py - church -> Cobblestone (b011_001a) item shop, standing at the shopkeeper, no vision.

jevplay's village route to the spot in front of the shop (-4.1, 3.8), then up, west to below the
door (-9.65, -4.3) and up through it; inside, to the left side of the counter facing the
shopkeeper (press A to talk). Leaves the session running. At the story point of user_citra
(evening after the Tor) she only chats; the shop menu is not offered."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az        # noqa: E402
import azctl     # noqa: E402
import walkto    # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "shopwalk")


def pulses(s, seq, log=print):
    for sx, sy, n in seq:
        for _ in range(n):
            a = walkto.pos(s)
            s.stick(sx, sy, 0.15)
            s.wait_game(s.game_time() + 0.25)
            b = walkto.pos(s)
            log(sx, sy, b.round(2))
            if np.linalg.norm(b - a) > 5:
                return True
    return False


def main(save=None):
    import jevplay
    from jevnav import Nav
    os.makedirs(OUT, exist_ok=True)
    sc = jevplay.SHOP
    azctl.main(["start", "--save", save or sc["save"], "--speed", "100", "--boot", "1"])
    s = azctl.Session()
    for d, secs in sc["moves"]:
        s.walk(d, secs)
    s.wait_game(s.game_time() + 1)
    nav = Nav(s)
    nav.no_jev = True
    for wp in sc["waypoints"]:
        print("waypoint", wp, nav.route(wp, "waypoint"), walkto.pos(s).round(2), flush=True)
    s.shot(os.path.join(OUT, "front.png"))
    # found 2026-10-13: the shop door is at about (-9.6, -5.7); stick up = -z, stick left = -x here
    pulses(s, [(0, 156, 6)])
    walkto.goto_world(s, np.array([-9.65, -4.3]), tol=0.3)
    if not pulses(s, [(0, 156, 10)]):
        print("not entered", walkto.pos(s).round(2))
        return False
    s.wait_game(s.game_time() + 2)
    s.shot(os.path.join(OUT, "inside.png"))
    print("ENTERED", walkto.pos(s).round(2))
    # shopkeeper (the woman behind the L-shaped counter): talk from the left side of the counter
    walkto.goto_world(s, np.array([-0.6, 1.5]), tol=0.25)
    s.stick(156, 0, 0.04)
    s.wait_game(s.game_time() + 0.4)
    s.shot(os.path.join(OUT, "at_counter.png"))
    return True


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
