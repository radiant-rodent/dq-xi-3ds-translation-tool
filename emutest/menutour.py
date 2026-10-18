"""menutour.py LABEL [SAVE] - open the X menu and every main entry (plus its first sub screen),
screenshot each, contact sheet in _emutest/runs/menutour/LABEL/sheet.png. Stops the session."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az      # noqa: E402
import azctl   # noqa: E402


def main(label, save="user_citra", entries=8):
    out = os.path.join(az.ROOT, "runs", "menutour", label)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*.png")):
        os.remove(f)
    azctl.main(["start", "--save", save, "--speed", "100", "--boot", "1"])
    s = azctl.Session()
    try:
        for i in range(entries):
            s.press("X", after=1.0)
            for _ in range(i):
                s.press("DOWN", after=0.3)
            s.shot(os.path.join(out, f"{i}_0menu.png"))
            s.press("A", after=1.2)
            s.shot(os.path.join(out, f"{i}_1open.png"))
            s.press("A", after=1.2)
            s.shot(os.path.join(out, f"{i}_2sub.png"))
            for _ in range(5):
                s.press("B", after=0.4)
            if s.crashed():
                print("CRASH in entry", i, s.crashed())
                break
        az.sheet(sorted(glob.glob(os.path.join(out, "*_*.png"))), os.path.join(out, "sheet.png"), cols=6, scale=0.4)
    finally:
        azctl.main(["stop"])


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:3])
