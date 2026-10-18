"""grammarstubtest.py - the grammar hook stubs (grammar_asm.STUB) in armsim, without the game.

- stub (end of pane SetString): resolves the pane text, except when SetString's caller (its
  saved lr) is the template walker 0x3A7594 (a piece of a text being composed).
- estub (after the walker's real pass): resolves the composed pane text.
- sstub (buffer sizing): measured length + 2 units per {the}/{The}/{a}/{A}/{pl} in the template.

    python emutest/grammarstubtest.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from armsim import Sim                                   # noqa: E402
from dq11tool import grammar_asm, grammar_code as gc     # noqa: E402
from dq11tool.textcodec import encode_str                # noqa: E402

PANE, BUF, TPL, SP = 0x10002000, 0x10003000, 0x10004000, 0x10008000


def setup(sim, text, cap=128):
    raw = encode_str(text).encode("utf-16-le") + b"\0\0"
    put = lambda a, b: sim.mem.__setitem__(slice(a - sim.mem_base, a - sim.mem_base + len(b)), b)
    put(BUF, bytes(cap * 2 + 8))
    put(BUF, raw)
    put(PANE, bytes(0x100))
    put(PANE + 0xa4, struct.pack("<I", BUF))
    put(PANE + 0xcc, struct.pack("<HH", cap, len(raw) // 2 - 1))
    put(PANE + 0xd0, b"\x07")
    put(PANE + 0x44, b"\x05")


def text(sim):
    o = BUF - sim.mem_base
    return bytes(sim.mem[o:o + 512]).decode("utf-16-le").split("\0")[0]


def length(sim):
    return struct.unpack_from("<H", sim.mem, PANE + 0xce - sim.mem_base)[0]


def main():
    labels = grammar_asm._label_addresses(grammar_asm._full_source())
    sim = Sim(gc.CODE, gc.CAVE)
    bad = 0

    def check(name, cond):
        nonlocal bad
        print(("ok   " if cond else "FAIL ") + name)
        bad += not cond

    raw_text = "{The}{noun}Slime takes 1 {n:point|points} of damage!"
    want = "The slime takes 1 point of damage!"
    # stub from an ordinary caller: SetString pushed {r4-r8, lr}, the hook's bl pushed nothing
    for caller, resolves in ((0x2E0630, True), (0x3A7924, False), (0x3A7A2C, True), (0x3A7593, True)):
        setup(sim, raw_text)
        sim.r = [0x300 + k for k in range(16)]
        sim.r[4], sim.r[5], sim.r[6] = PANE, 0, 7
        sp = SP - 24
        struct.pack_into("<6I", sim.mem, sp - sim.mem_base, 4, 5, 6, 7, 8, caller)
        sim.call(labels["stub"], sp=sp)
        got = text(sim)
        ok = (got == want and length(sim) == len(want) and sim.r[6] == len(want)) if resolves else \
             (got == encode_str(raw_text) and sim.r[6] == 7)
        check(f"stub, caller {caller:#x}: {'resolved' if resolves else 'left alone'} ({got!r}, r6 {sim.r[6]})",
              ok and sim.r[0] == 7 and sim.r[13] == sp and sim.r[4] == PANE)
    # estub: the composed text
    setup(sim, raw_text)
    sim.r = [0x300 + k for k in range(16)]
    sim.r[4] = PANE
    sim.call(labels["estub"], sp=SP)
    check(f"estub resolves ({text(sim)!r})", text(sim) == want and length(sim) == len(want)
          and sim.r[0] == 5 and sim.r[13] == SP and sim.r[1:4] == [0x301, 0x302, 0x303])
    # sstub: slack per marker (+2 units) and per {v:N} / party name code (+8 units)
    for tpl, slack in (("{The}{v:2} attacks {the}{v:3}{pl}!", 22), ("Hero attacks!", 0),
                       ("{a}{v:5}", 10), ("\ue010", 8)):
        raw = encode_str(tpl).encode("utf-16-le") + b"\0\0"
        o = TPL - sim.mem_base
        sim.mem[o:o + len(raw)] = raw
        sim.r = [0x300 + k for k in range(16)]
        sim.r[0], sim.r[6], sim.r[8] = 40, TPL, len(raw) // 2 - 1
        sim.call(labels["sstub"], sp=SP)
        check(f"sstub {tpl!r}: {sim.r[1]} (want {40 + slack})",
              sim.r[1] == 40 + slack and sim.r[2:4] == [0x302, 0x303] and sim.r[13] == SP)
    print("all ok" if not bad else f"{bad} FAILED")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
