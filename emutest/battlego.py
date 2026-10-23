"""battlego.py LABEL [SLIME_NAME] - into the first battle (Cobblestone Tor slime) and screenshot
the battle UI: command menu, target label, enemy list, result.

battleroute.py's replay, then (found 2026-10-13) walking east into the cave mouth loads the cave
(the story slime sequence starts inside); north with the D-pad until the slime is on screen,
then stick pulses toward it (found by colour) with A attack it. With SLIME_NAME the slime's name (source 5073) is
replaced in a test build (e.g. a long official name to see the squeezed labels) and restored
afterwards, normal build included. Screenshots: _emutest/runs/battlego/LABEL/.
"""
import glob
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az            # noqa: E402
import azctl         # noqa: E402
import battleroute   # noqa: E402
import build_test    # noqa: E402
import walkto        # noqa: E402

SLIME = 5073
STOP_AT_CAVE = "--cave" in sys.argv          # leave the session running at the cave entrance
DUMP = "--dump" in sys.argv                  # log the RAM strings with "lime" after every result
TEXTLOG = "--no-textlog" not in sys.argv     # every text shown -> report.html (emutest/textrec.py)
DUMP_RANGE = (0x31000000, 0x34000000)        # heap: pane buffers / battle messages (2026-10-13)
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace", "project.db")


def _toward_slime(path, hero=(200, 120)):
    """Stick (x, y) from the hero (about the top screen's centre) toward the slime: the only
    bright light-blue blob on the top screen. None when no slime is visible."""
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)[:240]
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    ys, xs = np.nonzero((b > 170) & (r < 110) & (g > 90) & (b - r > 90))
    if len(xs) < 15:
        return None
    dx, dy = xs.mean() - hero[0], hero[1] - ys.mean()      # screen up = stick up
    n = max(1.0, float(np.hypot(dx, dy)))
    return int(156 * dx / n), int(156 * dy / n)


def dump_strings(s, out, tag, needle="lime"):
    """Every UTF-16 string in DUMP_RANGE that contains needle, appended to out/strings.txt."""
    pat = needle.encode("utf-16-le")
    found = []
    lo, hi = DUMP_RANGE
    for a in range(lo, hi, 0x10000):
        try:
            d = b"".join(s.read(b, 0x1000) for b in range(a, a + 0x10000, 0x1000))
        except Exception:
            continue
        i = d.find(pat)
        while i >= 0:
            if i % 2 == 0:
                st = i
                while st >= 2 and d[st - 2:st] != bytes(2) and i - st < 400:
                    st -= 2
                en = d.find(bytes(2), i)
                while en >= 0 and en % 2:
                    en = d.find(bytes(2), en + 1)
                text = d[st:en if en >= 0 else i + 80].decode("utf-16-le", "replace")
                found.append(f"{a + st:08x}  {text!r}")
            i = d.find(pat, i + 2)
    with open(os.path.join(out, "strings.txt"), "a", encoding="utf-8") as fp:
        nl = chr(10)
        fp.write(f"== {tag}" + nl + nl.join(sorted(set(found), key=lambda x: x[10:])) + nl)


def fight(out, start=True, until_battle=False):
    """until_battle: return the session as soon as the battle has started."""
    battleroute.main(os.path.basename(out), start=start)
    s = azctl.Session()
    if TEXTLOG and not until_battle:
        import report
        import textrec
        rec = textrec.TextRecorder(s, out).start()
        try:
            return _fight(s, out, until_battle)
        finally:
            rec.stop()
            print("text log:", rec.count, "records,", rec.dropped, "lost ->", report.build(out))
    return _fight(s, out, until_battle)


def _fight(s, out, until_battle):
    # D-pad steps are fixed hops on the bottom map: get to (29.3, -25.3), the spot of the
    # working manual run, then the stick walk east crosses into the next area
    goal = np.array([29.3, -25.3])
    for _ in range(16):
        here = walkto.pos(s)
        d = goal - here
        print("dpad at", here.round(1), flush=True)
        if np.linalg.norm(d) < 1.0:
            break
        tries = (["RIGHT" if d[0] > 0 else "LEFT"] if abs(d[0]) > 1 else []) + \
                (["DOWN" if d[1] > 0 else "UP"] if abs(d[1]) > 1 else [])
        for b in tries + ["UP", "RIGHT", "DOWN", "LEFT"]:
            s.hold(b, 0.4)
            s.wait_game(s.game_time() + 0.3)
            if np.linalg.norm(walkto.pos(s) - here) > 0.3:
                break
    # east along the corridor into the cave mouth (about (38.5, -29.5) on the bottom map); the
    # next area loads there (the position jumps far away)
    start = walkto.pos(s)
    for t in ([30.5, -24.5], [36.0, -26.0], [38.5, -29.5], [40.0, -30.5]):
        try:
            walkto.goto_world(s, np.array(t), tol=0.5)
        except RuntimeError:
            pass                                # the map change stops the walk
        if np.linalg.norm(walkto.pos(s) - start) > 20:
            break
    print("after walk", walkto.pos(s).round(1), flush=True)
    if np.linalg.norm(walkto.pos(s) - start) <= 20:
        s.shot(os.path.join(out, "no_area_change.png"))
        print("did not reach the next area")
        return
    s.wait_game(s.game_time() + 1.5)
    for _ in range(3):                          # the tutorial pages (if already shown)
        s.press("A", after=1.0)
    # inside the cave: the entrance passage is blocked to the north; the way on is the corridor
    # east (D-pad, bottom map). Walking it starts the story scene (Gemma: "M-More monsters!")
    # with the slime west of the hero.
    print("in the cave at", walkto.pos(s).round(1), flush=True)
    s.shot(os.path.join(out, "cave.png"))
    if STOP_AT_CAVE:
        return
    for _ in range(6):
        here = walkto.pos(s)
        s.hold("RIGHT", 0.3)
        s.wait_game(s.game_time() + 0.3)
        if np.linalg.norm(walkto.pos(s) - here) < 0.2:
            break
    for _ in range(5):                          # Gemma's lines, the field attack tutorial
        s.press("A", after=1.0)
    s.shot(os.path.join(out, "scene.png"))
    # at the slime (found by colour on the top screen) with A; the battle has started when the
    # hero is placed on the battle field (x exactly 0) - stop steering then, the stick would
    # move the menu cursor
    for i in range(20):
        p = os.path.join(out, f"aim{i:02d}.png")
        s.shot(p)
        s.stick(*(_toward_slime(p) or (-156, 0)), 0.12)
        s.wait_game(s.game_time() + 0.15)
        s.press("A", after=0.5)
        if walkto.pos(s)[0] == 0.0:
            break
        if s.crashed():
            print("CRASH", s.crashed())
            return
    else:
        print("no battle")
        return
    if DUMP:                                    # the "A slime appears!" line comes and goes
        for k in range(5):
            s.wait_game(s.game_time() + 0.5)
            s.shot(os.path.join(out, f"0_start{k}.png"))
        s.wait_game(s.game_time() + 0.5)
    else:
        s.wait_game(s.game_time() + 2.5)
    s.shot(os.path.join(out, "1_appears.png"))
    if until_battle:
        return s
    s.press("A", after=2.0)                     # the battle message closes, the main menu opens
    s.shot(os.path.join(out, "2_main.png"))
    s.press("A", after=1.2)                     # Fight -> the hero's commands
    s.shot(os.path.join(out, "3_command.png"))
    s.press("A", after=1.2)                     # Attack -> the target label
    s.shot(os.path.join(out, "4_target.png"))
    for i in range(24):                         # through the fight (A = Fight / Attack)
        s.press("A", after=1.5)
        s.shot(os.path.join(out, f"5_result{i:02d}.png"))
        if DUMP:                                # top-screen lines come and go quickly
            for k in range(4):
                s.wait_game(s.game_time() + 0.35)
                s.shot(os.path.join(out, f"5_result{i:02d}_{k}.png"))
            dump_strings(s, out, f"5_result{i:02d}")
        if abs(walkto.pos(s)[0]) > 0.5:         # back on the field: the battle is over
            break
    print("crashed", s.crashed())


def main(label, name=None):
    out = os.path.join(az.ROOT, "runs", "battlego", label)
    os.makedirs(out, exist_ok=True)
    for f in (glob.glob(os.path.join(out, "*.png")) + glob.glob(os.path.join(out, "strings.txt")) +
              glob.glob(os.path.join(out, "events.*")) + glob.glob(os.path.join(out, "shots", "*.png"))):
        os.remove(f)
    db = sqlite3.connect(DB)
    orig = db.execute("SELECT translation FROM source WHERE id=?", (SLIME,)).fetchone()[0]
    try:
        if name:
            db.execute("UPDATE source SET translation=? WHERE id=?", (name.replace("\\n", "\n"), SLIME))
            db.commit()
        build_test.build(test_text_log=TEXTLOG)
        fight(out)
        az.sheet(sorted(glob.glob(os.path.join(out, "*.png"))), os.path.join(out, "sheet.png"), cols=5, scale=0.4)
    finally:
        if name:
            db.execute("UPDATE source SET translation=? WHERE id=?", (orig, SLIME))
            db.commit()
        if not STOP_AT_CAVE:
            azctl.main(["stop"])
        if name and not STOP_AT_CAVE:
            build_test.build()


if __name__ == "__main__":
    main(*[a for a in sys.argv[1:] if not a.startswith("--")][:2])
