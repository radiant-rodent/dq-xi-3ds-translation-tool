"""posdiff.py - differential search for the hero's stored position: snapshot RAM, walk, snapshot
again; report words whose change is proportional to the mirror's x (and the z next to it), for
float and int32 encodings. Run with a session started (azctl start --save user_citra)."""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import azctl    # noqa: E402
import walkto   # noqa: E402

RANGES = [(0x00700000, 0x00900000), (0x08000000, 0x10000000), (0x30000000, 0x38000000)]


def snap(s):
    out = {}
    for lo, hi in RANGES:
        for a in range(lo, hi, 0x40000):
            try:
                out[a] = b"".join(s.read(b, 0x1000) for b in range(a, a + 0x40000, 0x1000))
            except Exception:
                pass
    return out


def main():
    s = azctl.Session()
    s.interrupt = None
    p0 = walkto.pos(s)
    A = snap(s)
    pa = walkto.pos(s)
    for sx, sy in ((110, -110), (-110, -110), (110, 110), (-110, 110)):
        s.stick(sx, sy, 0.25)
        s.wait_game(s.game_time() + 0.5)
        pb = walkto.pos(s)
        if abs(pb[0] - pa[0]) > 0.3 and abs(pb[1] - pa[1]) > 0.3:
            break
    B = snap(s)
    pb2 = walkto.pos(s)
    print("mirror a", p0, pa, "b", pb, pb2, flush=True)
    dx, dz = float(pb[0] - pa[0]), float(pb[1] - pa[1])
    for a, da in A.items():
        db = B.get(a)
        if db is None:
            continue
        for kind in ("<f4", "<f8"):
            va = np.frombuffer(da, kind).astype(np.float64)
            vb = np.frombuffer(db, kind).astype(np.float64)
            with np.errstate(all="ignore"):
                kx = (vb - va) / dx
                for gap in (1, 2):
                    kz = (vb[gap:] - va[gap:]) / dz
                    m = np.isfinite(kx[:-gap]) & (np.abs(kx[:-gap]) > 1e-3) & (np.abs(kx[:-gap]) < 1e4) & (np.abs(kx[:-gap] - kz) < 0.05 * np.abs(kx[:-gap]))
                    for i in np.nonzero(m)[0][:20]:
                        print(kind, hex(a + 4 * int(i)), "gap", gap, "k", round(float(kx[i]), 4),
                              "val", va[i], vb[i], flush=True)


if __name__ == "__main__":
    main()
