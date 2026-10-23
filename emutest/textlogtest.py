"""textlogtest.py - the ARM text logger (dq11tool/textlog.py) in emutest/armsim.py against its
Python model (textlog.reference): dedupe, raw/resolved phases, ring wrap, record layout.

    python emutest/textlogtest.py [N_RANDOM]
"""
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from armsim import Sim                     # noqa: E402
from dq11tool import textlog                # noqa: E402

CAVE = 0x724200
MEM = 0x10000000
CTL = MEM + 0x100
TICK = MEM + 0x80
PANES = [MEM + 0x400 + 0x100 * k for k in range(4)] + [MEM + 0x400 + 0x4000]   # last: same slot as #0
BUF = MEM + 0x2000
RING = MEM + 0x10000
# stand-in for gfix: every 'a' becomes 'A' in place (r0 = buf, r1 = capacity)
FAKE_GFIX = r"""
    ldrh r2, [r0]
    cmp r2, #0
    bxeq lr
    cmp r2, #0x61
    moveq r2, #0x41
    strheq r2, [r0]
    add r0, r0, #2
    b {start}
"""


def build():
    import keystone
    body = textlog.assemble(CAVE, CTL, gfix=0)
    g = CAVE + len(body)
    body = textlog.assemble(CAVE, CTL, gfix=g)
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    return body + bytes(ks.asm(FAKE_GFIX.format(start=hex(g)), g)[0])


def read_ring(sim, start_pos, end_pos, size):
    m = sim.mem
    pos, out = start_pos, []
    while pos != end_pos:
        o = RING - MEM + pos
        sz = struct.unpack_from("<H", m, o)[0]
        if sz == 0 or pos + 2 > size:
            pos = 0
            continue
        out.append(textlog.decode(bytes(m[o:o + sz])))
        pos += sz
    return out


def run(calls, ring_size):
    sim = Sim(build(), CAVE, mem_size=0x30000, mem_base=MEM)
    struct.pack_into("<5I", sim.mem, CTL - MEM, RING, ring_size, 0, 0, TICK)
    struct.pack_into("<Q", sim.mem, TICK - MEM, 0x123456789A)
    for k, p in enumerate(PANES):
        name = f"txt_pane{k}".encode()
        sim.mem[p - MEM + 0x80:p - MEM + 0x80 + len(name)] = name
    got, prev = [], 0
    for pane, raw, frame, _ in calls:
        struct.pack_into("<I", sim.mem, CTL - MEM - 16, frame)
        data = raw.encode("utf-16-le") + b"\0\0"
        cap = len(raw) + 8
        sim.mem[BUF - MEM:BUF - MEM + len(data)] = data
        struct.pack_into("<I", sim.mem, pane - MEM + 0xA4, BUF)
        struct.pack_into("<H", sim.mem, pane - MEM + 0xCC, cap)
        sim.r = [0] * 16
        sim.r[4], sim.r[5] = pane, 3
        sim.call(CAVE, BUF, cap)
        pos, seq = struct.unpack_from("<II", sim.mem, CTL - MEM + 8)
        recs = read_ring(sim, prev, pos, ring_size)
        prev = pos
        for r in recs:
            assert r["tick"] == 0x123456789A and r["start"] == 3, r
            assert r["name"] == f"txt_pane{PANES.index(r['pane'])}", r
        got += recs
    return got


def check(calls, ring_size=0x1000):
    got = run(calls, ring_size)
    want = textlog.reference(calls)
    g = [(r["pane"], r["phase"], r["text"], r["frame"]) for r in got]
    w = [(r["pane"], r["phase"], r["text"], r["frame"]) for r in want]
    assert g == w, f"\nARM  {g}\nwant {w}"
    assert [r["seq"] for r in got] == list(range(len(got)))
    return len(got)


def main(n_random=300):
    P = PANES
    fixed = [
        (P[0], "Hello", 1, "Hello"),
        (P[0], "Hello", 2, "Hello"),            # refreshed next frame: no record
        (P[0], "Hello", 3, "Hello"),
        (P[0], "Hello", 10, "Hello"),           # again after a pause: logged
        (P[1], "a slime", 10, "A slime"),       # raw + resolved
        (P[0], "Bye", 11, "Bye"),
        (P[4], "Bye", 11, "Bye"),               # other pane, same dedupe slot
        (P[0], "Bye", 12, "Bye"),               # slot was taken by P[4]: logged again
        (P[3], "", 20, ""), (P[3], "57", 20, "57"), (P[3], "57G", 20, "57G"),    # built up every frame
        (P[3], "", 21, ""), (P[3], "57", 21, "57"), (P[3], "57G", 21, "57G"),    # -> logged once
        (P[3], "57G", 30, "57G"),                                         # after a pause: again
        (P[2], "", 13, ""),
    ]
    print("fixed:", check(fixed), "records")
    rnd = random.Random(1)
    calls, frame = [], 0
    for _ in range(n_random):
        frame += rnd.choice((0, 1, 1, 2, 3, 5))
        t = "".join(rnd.choice("ab \nxyz") for _ in range(rnd.randint(0, 60)))
        calls.append((rnd.choice(P), t, frame, t.replace("a", "A")))
        if rnd.random() < 0.4:
            calls.append(calls[-1][:2] + (frame + rnd.randint(0, 4), calls[-1][3]))
    print("random (ring wraps):", check(calls, 0x400), "records")
    print("ok, code", len(textlog.assemble()), "bytes")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
