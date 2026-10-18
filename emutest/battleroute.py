"""battleroute.py [LABEL] - first battle (Cobblestone Tor slimes) from the cobblestone_start save.

Replays _emutest/tools/first_battle_route.txt (Citra-era route, "T:DUR:BUTTON" in emulated
seconds) live: every step is pressed when the game clock reaches T, so loading-time
differences between emulators do not shift the inputs much. D-pad moves follow the bottom map
(camera independent), circle pad steps (CU, CUL, ...) the camera. Screenshots every 10 game
seconds after T=380 into _emutest/runs/battleroute/LABEL/; the session keeps running at the
end (the battle should be on screen).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az        # noqa: E402
import azctl     # noqa: E402

ROUTE = os.path.join(az.ROOT, "tools", "first_battle_route.txt")
PAD = {"UP", "DOWN", "LEFT", "RIGHT"}


def steps():
    for item in open(ROUTE).read().strip().split(","):
        t, dur, b = item.split(":")
        yield float(t), float(dur), b


def main(label="now", start=True):
    """start=False: replay on the already running session (e.g. one started with the GDB stub)."""
    out = os.path.join(az.ROOT, "runs", "battleroute", label)
    os.makedirs(out, exist_ok=True)
    if start:
        azctl.main(["start", "--save", "cobblestone_start"])
    s = azctl.Session()
    next_shot = 380.0
    for t, dur, b in steps():
        while s.game_time() < t:
            if s.game_time() >= next_shot:
                s.shot(os.path.join(out, f"t{int(next_shot):04d}.png"))
                next_shot += 10
            s.wait_game(min(t, s.game_time() + 0.5))
        if b in az.CIRCLE:
            s.stick(*az.CIRCLE[b], dur)
        elif b in PAD and dur > 0.2:
            s.hold(b, dur)
        else:
            s.press(b, after=0.05)
        if s.crashed():
            print("CRASH at", t, s.crashed())
            return
    s.wait_game(s.game_time() + 3)
    s.shot(os.path.join(out, "end.png"))
    print("route done at", round(s.game_time(), 1))


if __name__ == "__main__":
    main(*sys.argv[1:2])
