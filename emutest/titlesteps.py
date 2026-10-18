"""titlesteps.py - run button steps from the title screen, log every text (no scenario event).

  python emutest/titlesteps.py [--build] [--code N2Ypph!7uzprZQ8Ms] "wait:4,A,wait:3,A,shot:menu"

STEPS: comma list of buttons (A B X Y UP DOWN LEFT RIGHT START ...), wait:SECONDS, shot:NAME,
code (types --code on the title's Save Code keyboard, cursor on its first key, then Done).
The run uses the scenario save (_scenario, from the last scenario.py run) and the mod already
installed; --build exports the test mod first. Output: _emutest/runs/titlesteps/ (events
printed at the end, shots/). AFTER env: seconds after each press (default 0.6).

Save Code entry from the title, e.g.:
  "wait:4,A,wait:3,A,wait:2,DOWN,DOWN,DOWN,A,wait:3,A,wait:3,code,wait:3,shot:result"
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import az        # noqa: E402
import azctl     # noqa: E402
import textrec   # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "titlesteps")


def code_presses(code: str) -> list[str]:
    """Cursor moves + A for each character of a Save Code on keyboard page 1, then Done."""
    from dq11tool import namekbd
    pos = {}
    for r, line in enumerate(namekbd.PAGE_LATIN):
        for c, ch in enumerate(line):
            pos.setdefault(ch, (r, c))
    out, cur = [], (0, 0)
    for ch in list(code) + ["DONE"]:
        r, c = (5, 10) if ch == "DONE" else pos[ch]
        dr, dc = r - cur[0], c - cur[1]
        out += ["DOWN"] * dr if dr > 0 else ["UP"] * -dr
        out += ["RIGHT"] * dc if dc > 0 else ["LEFT"] * -dc
        out.append("A")
        cur = (r, c)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("steps")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--code", default="")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.build:
        import build_test
        build_test.build(test_text_log=True)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    if os.path.exists(azctl.STATE):
        try:
            azctl.main(["stop"])
        except Exception:
            pass
    azctl.main(["start", "--save", "_scenario"])
    s = azctl.Session()
    rec = textrec.TextRecorder(s, OUT)
    s.wait_game(6)
    rec.start()
    after = float(os.environ.get("AFTER", "0.6"))
    steps = []
    for st in a.steps.split(","):
        st = st.strip()
        steps += code_presses(a.code) if st == "code" else [st]
    crash = None
    for st in steps:
        if st.startswith("wait:"):
            s.wait_game(s.game_time() + float(st[5:]))
        elif st.startswith("shot:"):
            rec.shot(st[5:])
        elif st:
            crash = s.press(st, after=after) or s.crashed()
        if crash:
            print("CRASH", crash)
            break
    s.wait_game(s.game_time() + 1)
    rec.shot("end")
    rec.stop()
    azctl.main(["stop"])
    for e in rec.events:
        if "seq" in e and e.get("text"):
            print(f"{e.get('t') or 0:8.2f} {e.get('name', ''):18} {e['text'][:80]!r}")
    return 1 if crash else 0


if __name__ == "__main__":
    sys.exit(main())
