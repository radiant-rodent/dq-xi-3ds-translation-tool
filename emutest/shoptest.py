"""shoptest.py LABEL [SAVE] - shop crash test: shopwalk to the Cobblestone shopkeeper (save
user_shop: story far enough for the shop to sell), talk, open Buy, scroll every item (item
details on the bottom screen), back out, open Sell, scroll, quit. Screenshots and a contact sheet
go to _emutest/runs/shoptest/LABEL/; with heap probes in the build it prints memreport's table.
Stops the session at the end."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az          # noqa: E402
import azctl       # noqa: E402
import memreport   # noqa: E402
import shopwalk    # noqa: E402
import walkto      # noqa: E402
from jevplay import boxes, talk_window  # noqa: E402

import numpy as np  # noqa: E402

SEQ = ("A,A,A," + "DOWN," * 6 + "UP,UP,B,B,"       # greeting, Buy, all items, back
       "DOWN,A,A," + "DOWN," * 6 + "B,B,DOWN,A,A")      # Sell, the bag, back, Quit


def main(label, save="user_shop"):
    out = os.path.join(az.ROOT, "runs", "shoptest", label)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*.png")):
        os.remove(f)
    try:
        if not shopwalk.main(save):
            print(label, "did not reach the shop")
            return
        s = azctl.Session()
        # the greeting must be on screen before anything else is pressed (otherwise the D-pad
        # presses below walk the hero away and the run proves nothing)
        for attempt in range(4):
            walkto.goto_world(s, np.array([-0.6, 1.5]), tol=0.2)
            s.stick(156, 0, 0.04)
            s.wait_game(s.game_time() + 0.4)
            s.press("A", after=1.2)
            p = os.path.join(out, "talk.png")
            s.shot(p)
            if talk_window(p):
                break
        else:
            print(label, "INVALID: the shopkeeper's greeting never appeared")
            return
        result, menus = "ok", 0
        for i, b in enumerate(SEQ.split(",")):
            s.press(b, after=1.0)
            s.shot(os.path.join(out, f"{i:02d}_{b}.png"))
            menus += boxes(os.path.join(out, f"{i:02d}_{b}.png")) > 0
            c = s.crashed()
            if c:
                result = f"CRASH after step {i} ({b}): {c}"
                break
        if result == "ok" and menus < 10:
            result = f"INVALID: only {menus} screenshots show shop windows"
        print(label, result, f"({menus} screenshots with shop windows)", flush=True)
        try:
            for row in memreport.report(s.read):
                print("  %-8s size %8d live %8d peak %8d" % row[:4])
        except Exception as e:
            print("  (no heap probes)", str(e)[:80])
        az.sheet(sorted(glob.glob(os.path.join(out, "*.png"))), os.path.join(out, "sheet.png"), cols=6, scale=0.4)
    finally:
        azctl.main(["stop"])


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:3])
