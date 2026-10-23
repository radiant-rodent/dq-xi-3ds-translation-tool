"""posprobe.py - find the hero position the game really keeps (0x7F3110 is only a per-frame copy).

Boots user_citra, scans RAM for (x, z) float pairs equal to the mirror, then writes each
candidate (all copies, several frames in a row) and checks whether the mirror follows.
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import azctl    # noqa: E402
import walkto   # noqa: E402

RANGES = [(0x00700000, 0x00900000), (0x08000000, 0x0C000000), (0x14000000, 0x1C000000), (0x30000000, 0x34000000)]


def scan(s, x, z):
    hits = []
    for lo, hi in RANGES:
        for a in range(lo, hi, 0x40000):
            try:
                d = np.frombuffer(b"".join(s.read(b, 0x1000) for b in range(a, a + 0x40000, 0x1000)), dtype="<f4")
            except Exception:
                continue
            with np.errstate(all="ignore"):
                for gap in (1, 2):
                    m = (np.abs(d[:-gap] - x) < 0.02) & (np.abs(d[gap:] - z) < 0.02)
                    hits += [(a + 4 * int(i), gap) for i in np.nonzero(m)[0]]
    return hits


def main():
    if "--here" not in sys.argv:
        azctl.main(["start", "--save", "user_citra", "--speed", "100", "--boot", "1"])
    s = azctl.Session()
    s.walk("R", 0.5)
    s.wait_game(s.game_time() + 1)
    x, y, z = struct.unpack("<3f", s.read(walkto.POS, 12))
    hits = [h for h in scan(s, x, z) if h[0] != walkto.POS]
    print("mirror", round(x, 3), round(y, 3), round(z, 3), "candidates", [(hex(a), g) for a, g in hits], flush=True)
    for a, gap in hits:
        before = walkto.pos(s)
        cx = struct.unpack("<f", s.read(a, 4))[0]
        for _ in range(6):                      # hold the value a few frames
            s.write(a, struct.pack("<f", cx + 2.0))
            s.wait_game(s.game_time() + 0.05)
        s.wait_game(s.game_time() + 0.3)
        after = walkto.pos(s)
        moved = float(after[0] - before[0])
        print(hex(a), gap, "mirror dx", round(moved, 3), "<== MASTER" if abs(moved - 2.0) < 0.5 else "", flush=True)


if __name__ == "__main__":
    main()
