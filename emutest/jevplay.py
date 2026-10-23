"""jevplay.py SCENARIO [--steps N] - play the game to a scene without an agent in the loop.

Movement is code (walkto: closed loop on the hero's world position). Decisions at the goal
(which way to face, press A, step closer, close a window...) are made by Jev, OpenCode's free
"System One" decision model (https://opencode.ai/inference/v1/systemone): it gets a text state
built from sensors (position, distance to the goal, message window / menu boxes on screen,
the result of the last action) and picks one action from a fixed list. ~1 s and free per
decision. The OpenCode key is the one saved in the translation tool (mt.get_key).

Everything goes to _emutest/runs/jev/: log.txt, one screenshot per decision, final.png.
Scenarios: shop (Cobblestone item shop, talk to the shopkeeper; save user_citra).
Start with a fresh session: the script starts its own (speed 100 %).
"""
import json
import math
import os
import struct
import sys
import time

import httpx
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import az        # noqa: E402
import azctl     # noqa: E402
import walkto    # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "jev")
from jevplay_api import jev, jev_key   # noqa: E402
from jevnav import Nav                 # noqa: E402


# ---------------------------------------------------------------------- sensors
def talk_window(path: str) -> bool:
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    l, r = im[158:228, 27].min(1), im[158:228, 372].min(1)
    return ((l > 150).mean() + (r > 150).mean()) / 2 > 0.8


def boxes(path: str) -> int:
    """Rows of long white frame lines on the top screen (menus, shop windows, prompts)."""
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)[:240]
    white = im.min(2) > 200
    return int(sum(1 for y in range(240) if white[y].sum() > 120))


class Player:
    def __init__(self, s, log):
        self.s, self.log = s, log
        self.basis = None
        self.n = 0

    def shot(self, tag=""):
        self.n += 1
        p = os.path.join(OUT, f"{self.n:03d}{tag}.png")
        self.s.shot(p)
        return p

    def face(self, world_dir):
        """Turn toward a world direction (x, z) without walking off."""
        here = walkto.pos(self.s)
        if self.basis is None:
            self.basis = walkto.stick_basis(self.s)
            walkto.goto_world(self.s, here, tol=0.2)
        inv = np.linalg.inv(self.basis.T)
        sx, sy = inv @ np.asarray(world_dir, float)
        n = math.hypot(sx, sy) or 1
        self.s.stick(int(156 * sx / n), int(156 * sy / n), 0.04)
        self.s.wait_game(self.s.game_time() + 0.3)

    def reset_camera(self):
        self.s.write(self.s.MAILBOX + 20, struct.pack("<II", self.s._mask("L") | self.s._mask("R"), 4))
        self.s.wait_game(self.s.game_time() + 1.0)
        self.basis = None


# ---------------------------------------------------------------------- scenarios
SHOP = {
    "save": "user_citra",
    # church exit (camera-relative moves that work from the save point)
    "moves": [("R", 0.8), ("D", 2.5), ("R", 2.0)],
    # village waypoints (world x, z): past the old woman, over the bridge, to the shop door
    "waypoints": [(-20.45, -16.93), (-13.45, -16.96), (-13.59, -10.84), (-8.27, -2.07), (-4.06, 3.84)],
    "door_dir": (0.0, -1.0),        # the door is north of the last waypoint
    # inside: in front of the counter, the shopkeeper is to the north (-z)
    "goal": (3.73, 3.03),
    "target_dir": (0.0, -1.0),
    "goal_text": "Open the shop: talk to the shopkeeper behind the counter (the shop menu with "
                 "Buy / Sell appears).",
}
SCENARIOS = {"shop": SHOP}

ACTIONS = {
    "face_target": "turn toward the shopkeeper (north, across the counter)",
    "press_A": "press A: talk / advance the message / confirm the menu choice",
    "press_B": "press B: close a message or menu",
    "step_closer": "walk to the spot in front of the counter again",
    "reset_camera": "press L+R: reset the camera behind the hero",
    "done": "the goal is reached (a shop menu is open), stop",
}


def run(name: str, steps: int = 20):
    sc = SCENARIOS[name]
    os.makedirs(OUT, exist_ok=True)
    logf = open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8")

    def log(*a):
        line = " ".join(str(x) for x in a)
        print(line)
        logf.write(line + "\n")
        logf.flush()

    key = jev_key()
    azctl.main(["start", "--save", sc["save"], "--speed", "100", "--boot", "1"])
    s = azctl.Session()
    p = Player(s, log)
    for d, secs in sc["moves"]:
        s.walk(d, secs)
    s.wait_game(s.game_time() + 1)
    nav = Nav(s, log)
    for wp in sc["waypoints"]:
        log("waypoint", wp, nav.route(wp, f"the waypoint at x={wp[0]}, z={wp[1]}"), walkto.pos(s).round(2))
    import vision
    shot = p.shot("_door")
    loc = vision.locate(shot, "the entrance door of the item shop (house with a bag sign)")
    log("muse door:", loc)
    if loc:
        tgt = nav.map_target(loc[0], loc[1])
        log("door target", tgt.round(2), nav.route(tgt, "the shop entrance", tol=0.8, max_steps=30))
    if not nav.find_door("the item shop (the house right next to the hero)"):
        log("could not enter the building")
        return
    s.wait_game(s.game_time() + 1.5)
    nav.reset_camera()
    log("counter:", nav.route(sc["goal"], "the spot in front of the shop counter", tol=0.3), walkto.pos(s).round(2))
    last = "arrived in front of the counter"
    facing = None
    prev_img = None
    for i in range(steps):
        if s.crashed():
            log("CRASH", s.crashed())
            break
        shot = p.shot()
        here = walkto.pos(s)
        dist = float(np.linalg.norm(here - np.array(sc["goal"])))
        win, nbox = talk_window(shot), boxes(shot)
        state = (f"Goal: {sc['goal_text']}\n"
                 f"Hero distance to the spot in front of the counter: {dist:.2f} units.\n"
                 f"A message window is {'open' if win else 'not open'}. "
                 f"Framed menu boxes visible on the top screen: {'yes' if nbox > 2 else 'no'} ({nbox}).\n"
                 f"Last action: {last}.")
        try:
            if nav.no_jev:
                raise RuntimeError("no jev")
            a = jev(state, {"next": {"type": "choice", "instructions": "Which action should the player take next?",
                                     "criteria": ACTIONS}}, key)["next"]
        except Exception:
            nav.no_jev = True       # rules: face the shopkeeper, talk, stop when a menu is open
            if nbox > 2 and not win:
                a = {"choice": "done"}
            elif win:
                a = {"choice": "press_A"}
            elif facing is None or not changed:
                a = {"choice": "face_target" if facing is None else "step_closer"}
            else:
                a = {"choice": "press_A"}
        act = a["choice"]
        log(f"step {i}: {state!r} -> {act} {a.get('probabilities')}")
        if act == "done":
            break
        if act == "face_target":
            nav.face(sc["target_dir"])
            facing = f"toward the shopkeeper (x={dx:+.0f}, z={dz:+.0f})"
        elif act == "press_A":
            s.press("A", after=1.5)
        elif act == "press_B":
            s.press("B", after=1.0)
        elif act == "step_closer":
            a0 = walkto.pos(s)
            nav.route(sc["goal"], "the spot in front of the shop counter", tol=0.3, max_steps=10)
            mv = walkto.pos(s) - a0
            if np.linalg.norm(mv) > 0.05:
                facing = f"the direction it last walked (x={mv[0]:+.2f}, z={mv[1]:+.2f})"
        elif act == "reset_camera":
            nav.reset_camera()
        last = ACTIONS[act]
    final = os.path.join(OUT, "final.png")
    s.shot(final)
    log("final", final, "crash:", s.crashed())


if __name__ == "__main__":
    a = sys.argv[1:]
    steps = int(a[a.index("--steps") + 1]) if "--steps" in a else 20
    run(a[0], steps)
