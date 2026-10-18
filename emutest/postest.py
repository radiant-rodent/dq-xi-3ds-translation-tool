"""postest.py ADDR... - write x+2 into each candidate (held 6 frames) and report whether the
hero (mirror 0x7F3110) follows. Session must be running."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import azctl    # noqa: E402
import walkto   # noqa: E402

s = azctl.Session()
for arg in sys.argv[1:]:
    addrs = [int(a, 16) for a in arg.split("+")]
    before = walkto.pos(s)
    vals = [struct.unpack("<f", s.read(a, 4))[0] for a in addrs]
    for _ in range(6):
        for a, v in zip(addrs, vals):
            s.write(a, struct.pack("<f", v + 2.0))
        s.wait_game(s.game_time() + 0.05)
    s.wait_game(s.game_time() + 0.5)
    after = walkto.pos(s)
    dx = float(after[0] - before[0])
    print(arg, "dx", round(dx, 3), "dz", round(float(after[1] - before[1]), 3), "<== MOVES HERO" if abs(dx - 2) < 0.6 else "", flush=True)
