"""Jev routes the hero: every step Jev (OpenCode's free decision model) picks a compass
direction or a button from a text state; this module only executes one short step and
measures what happened.

State per step: goal text, hero position, target position, vector to the target, the last
moves with their effect (moved / blocked / map changed), the directions already blocked at
the current spot. The stick -> world mapping follows the camera: its rotation is re-estimated
after every step that moved.

    from jevnav import Nav
    nav = Nav(session, log)
    nav.route(target_xz, "the shop door", max_steps=60)      # -> "arrived" / "map_changed" / ...
"""
import math
import struct
from collections import deque

import numpy as np

import walkto
from jevplay_api import jev, jev_key

DIRS = {"north": (0, -1), "north-east": (1, -1), "east": (1, 0), "south-east": (1, 1),
        "south": (0, 1), "south-west": (-1, 1), "west": (-1, 0), "north-west": (-1, -1)}
STEP = 0.35          # game seconds of stick per step (~1.4 units)


def _ang(v):
    return math.atan2(v[1], v[0])


class Nav:
    def __init__(self, s, log=print):
        self.s, self.log = s, log
        self.key = jev_key()
        self.theta = None          # world angle of "stick right"
        self.hand = 1.0            # +1: stick up is 90 deg counter-clockwise of right in world x/z
        self.history = deque(maxlen=8)
        self.blocked: dict[tuple, set] = {}
        self.step_len = 1.4         # measured distance of the last moving step
        self.secs = STEP
        self.no_jev = False

    # ---------------------------------------------------------------- stick mapping
    def calibrate(self):
        b = walkto.stick_basis(self.s)          # rows: world motion of stick right, stick up
        self.theta = _ang(b[0])
        cross = b[0][0] * b[1][1] - b[0][1] * b[1][0]
        self.hand = 1.0 if cross > 0 else -1.0

    def _stick_for(self, d):
        """Stick (x, y) that should move the hero in world direction d."""
        a = _ang(d) - self.theta
        return math.cos(a), self.hand * math.sin(a)

    def step(self, name: str) -> str:
        d = np.array(DIRS[name], float)
        d /= np.linalg.norm(d)
        sx, sy = self._stick_for(d)
        a = walkto.pos(self.s)
        self.s.stick(int(156 * sx), int(156 * sy), self.secs)
        self.s.wait_game(self.s.game_time() + 0.15)
        b = walkto.pos(self.s)
        m = b - a
        dist = float(np.linalg.norm(m))
        if dist > 5:
            self.theta = None                    # new map, new camera
            self.blocked.clear()
            return "map_changed"
        progress = float(m @ d)
        # sliding along a wall moves the hero sideways: only progress in the wanted direction counts
        if dist < 0.25 or progress < 0.5 * dist or progress < 0.3:
            self.blocked.setdefault(self._cell(a), set()).add(name)
            return "blocked" if dist < 0.25 else "blocked (slid sideways along an obstacle)"
        self.step_len = dist
        # camera follow-up: rotate the mapping by the error between wanted and actual direction
        err = (_ang(m) - _ang(d) + math.pi) % (2 * math.pi) - math.pi
        self.theta += err * 0.7
        return "moved"

    @staticmethod
    def _cell(p):
        return (round(float(p[0])), round(float(p[1])))

    # ---------------------------------------------------------------- routing
    def route(self, target, goal: str, max_steps: int = 60, tol: float = 1.0,
              stop_on_map_change: bool = True, hint: str = "") -> str:
        target = np.asarray(target, float)
        if self.theta is None:
            self.calibrate()
        for i in range(max_steps):
            here = walkto.pos(self.s)
            v = target - here
            dist = float(np.linalg.norm(v))
            if dist < tol:
                return "arrived"
            # shorter steps close to the target (one full step is ~1.5-2.5 units)
            self.secs = STEP if dist > 3 else STEP * max(0.35, dist / 3)
            blocked = sorted(self.blocked.get(self._cell(here), set()))
            hist = "; ".join(self.history) or "none yet"
            # what each direction would do (Jev is weak at turning numbers into a direction)
            stepl = min(self.step_len, max(0.4, dist))
            opts = []
            for k, dv in DIRS.items():
                u = np.array(dv, float) / np.linalg.norm(dv)
                opts.append((float(np.linalg.norm(target - (here + stepl * u))), k))
            opts.sort()
            option_text = ", ".join(f"{k} -> {d:.1f}{' (BLOCKED here before)' if k in blocked else ''}"
                                    for d, k in opts)
            state = (f"Task: walk the hero to {goal}.\n"
                     f"Coordinates: x grows to the east, z grows to the south (north = smaller z).\n"
                     f"Hero position: x={here[0]:.1f}, z={here[1]:.1f}. Target: x={target[0]:.1f}, z={target[1]:.1f}.\n"
                     f"Target is {abs(v[0]):.1f} units {'east' if v[0] > 0 else 'west'} and "
                     f"{abs(v[1]):.1f} units {'south' if v[1] > 0 else 'north'} (distance {dist:.1f}). "
                     f"One step moves about 1.4 units.\n"
                     f"Distance to the target after one step in each direction (best first): {option_text}.\n"
                     + (f"{hint}\n" if hint else "")
                     + (f"Spots where every way toward the target was blocked: "
                        f"{', '.join(f'x={c[0]},z={c[1]}' for c, ds in self.blocked.items() if len(ds) >= 3)}.\n"
                        if any(len(ds) >= 3 for ds in self.blocked.values()) else "") +
                     f"Last moves (oldest first): {hist}.")
            ans = self._decide(state, opts, blocked, {"dir": {"type": "choice",
                                      "instructions": "Which direction should the hero step next? Normally take the "
                                                      "direction with the smallest distance after the step. Never take "
                                                      "a direction marked BLOCKED; if the best ones are blocked, take "
                                                      "the best unblocked one to get around the obstacle. Do not "
                                                      "undo the previous move unless everything else is blocked.",
                                      "criteria": {k: f"step {k}" for k in DIRS}}})
            name = ans["choice"]
            res = self.step(name)
            after = walkto.pos(self.s)
            self.history.append(f"{name} from ({here[0]:.1f},{here[1]:.1f}) -> {res}"
                                + (f" to ({after[0]:.1f},{after[1]:.1f})" if res == "moved" else ""))
            self.log(f"nav {i}: {name} {res} pos {after.round(2)} dist {np.linalg.norm(target - after):.2f}")
            if res == "map_changed" and stop_on_map_change:
                return "map_changed"
        return "gave_up"

    def _decide(self, state, opts, blocked, questions):
        """Jev's answer; without Jev (quota used up) the best unblocked option, not undoing
        the previous move."""
        if not self.no_jev:
            try:
                return jev(state, questions, self.key)["dir"]
            except Exception as e:
                self.log("jev unavailable, choosing by distance:", str(e)[:100])
                self.no_jev = True
        last = self.history[-1].split(" ")[0] if self.history else None
        undo = {"north": "south", "south": "north", "east": "west", "west": "east",
                "north-east": "south-west", "south-west": "north-east",
                "north-west": "south-east", "south-east": "north-west"}.get(last)
        for _, k in opts:
            if k not in blocked and k != undo:
                return {"choice": k}
        return {"choice": opts[0][1]}

    def find_door(self, what: str, reach: int = 6, max_tries: int = 8) -> bool:
        """The hero is near a door (a map change) in an unknown direction. Jev picks the next
        direction to try from the list of tries so far; each try walks up to `reach` short
        steps that way and, without a map change, walks back the same way it came."""
        if self.theta is None:
            self.calibrate()
        origin = walkto.pos(self.s)
        tried: list[str] = []
        for _ in range(max_tries):
            left = [k for k in DIRS if k not in [t.split(":")[0] for t in tried]]
            if not left:
                break
            state = (f"Task: find the entrance of {what}. It is within a few steps of the hero, direction "
                     f"unknown; walking through it loads the interior (a map change).\n"
                     f"Tries so far from this spot (direction: result): {'; '.join(tried) or 'none'}.\n"
                     f"Directions not tried yet: {', '.join(left)}.")
            name = left[0]
            if not self.no_jev:
                try:
                    ans = jev(state, {"dir": {"type": "choice",
                                              "instructions": "Which untried direction should the hero try next?",
                                              "criteria": {k: f"try {k}" for k in left}}}, self.key)["dir"]
                    name = ans["choice"] if ans["choice"] in left else left[0]
                except Exception:
                    self.no_jev = True
            moves, res = [], "blocked at once"
            for i in range(reach):
                a = walkto.pos(self.s)
                r = self.step(name)
                if r == "map_changed":
                    self.log(f"door: {name} -> map changed")
                    return True
                moves.append(walkto.pos(self.s) - a)
                if r.startswith("blocked"):
                    res = f"blocked after {i} steps" if i else "blocked at once"
                    break
            else:
                res = f"walked {reach} steps, no door"
            self.log(f"door try {name}: {res}")
            tried.append(f"{name}: {res}")
            walkto.goto_world(self.s, origin, tol=0.3)       # back to the start spot
        return False

    def map_target(self, map_x, map_y, scale=10.0, hero=(200, 360)):
        """World point of a bottom-map pixel (the map turns with the camera: map up = stick up)."""
        if self.theta is None:
            self.calibrate()
        sx, sy = (map_x - hero[0]) / scale, -(map_y - hero[1]) / scale
        r = np.array([math.cos(self.theta), math.sin(self.theta)])
        u = np.array([math.cos(self.theta + self.hand * math.pi / 2), math.sin(self.theta + self.hand * math.pi / 2)])
        return walkto.pos(self.s) + sx * r + sy * u

    def face(self, d):
        if self.theta is None:
            self.calibrate()
        sx, sy = self._stick_for(np.asarray(d, float) / np.linalg.norm(d))
        self.s.stick(int(156 * sx), int(156 * sy), 0.04)
        self.s.wait_game(self.s.game_time() + 0.3)

    def reset_camera(self):
        self.s.write(self.s.MAILBOX + 20, struct.pack("<II", self.s._mask("L") | self.s._mask("R"), 4))
        self.s.wait_game(self.s.game_time() + 1.0)
        self.theta = None
