"""namefixtest.py - the ARM name expansion (dq11tool/namefix.py) against namefix.resolve, in
emutest/armsim.py: fixed cases plus random text with name codes, small capacities included.

    python emutest/namefixtest.py [N_RANDOM]
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from armsim import Sim                     # noqa: E402
from dq11tool import namefix               # noqa: E402

BUF = 0x10000100
NAMES = ["Veronica", "Sylvando", "Hendrik", "King Irwin", "Kurucchi", "Stranger"]


def run_arm(sim, text: str, cap: int) -> str:
    raw = text.encode("utf-16-le") + b"\0\0"
    o = BUF - sim.mem_base
    sim.mem[o:o + cap * 2 + 64] = bytes(cap * 2 + 64)
    sim.mem[o:o + len(raw)] = raw
    sim.r = [0x11110000 + k for k in range(16)]
    keep = list(sim.r[4:12])
    sim.call(namefix.CAVE, BUF, cap)
    assert sim.r[4:12] == keep, "callee-saved registers changed"
    return bytes(sim.mem[o:o + cap * 2]).decode("utf-16-le").split("\0")[0]


def main(n_random=3000):
    assert namefix.CODE == namefix.assemble(), "namefix.CODE is stale: python -m dq11tool.namefix --write"
    codes = namefix.assign(NAMES)
    blob = (namefix.CODE.ljust(namefix.TABLE - namefix.CAVE, b"\0") + namefix.table(codes)
            ).ljust(namefix.TABLE_END - namefix.CAVE, b"\0")     # zero padded, as patch() writes it
    sim = Sim(blob, namefix.CAVE)
    c = list(codes.values())
    tests = [(f"{c[0]} casts Heal!", 256), (f"{c[1]} and {c[2]}", 256), (c[3], 256), ("", 256),
             (f"{c[0]}{c[0]}{c[0]}", 12), (f"Hi {c[1]}!", 6), (chr(0xE01F) + "x", 256),
             (chr(0xE000) + c[4] + chr(0xE00F), 256)]
    rnd = random.Random(3)
    pieces = c + [" ", "a", "!", "\n", chr(0xE000), chr(0xE020), chr(0xE01F), "Hero"]
    for _ in range(n_random):
        tests.append(("".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 12))), rnd.choice([256, 30, 9, 3])))
    bad = 0
    for raw, cap in tests:
        raw = raw[:cap - 1]
        want = namefix.resolve(raw, codes, cap)
        got = run_arm(sim, raw, cap)
        if got != want:
            bad += 1
            if bad <= 10:
                print("MISMATCH", repr(raw), cap, repr(want), repr(got))
    print(f"{len(tests)} cases, {bad} mismatches, {len(namefix.CODE)} bytes")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3000))
