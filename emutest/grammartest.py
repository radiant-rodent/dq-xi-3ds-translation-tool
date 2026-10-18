"""grammartest.py - the ARM grammar pass (dq11tool/grammar_asm.py) against the Python reference
(grammar.resolve), in emutest/armsim.py: fixed cases plus random marker soup.

    python emutest/grammartest.py [N_RANDOM]
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from armsim import Sim                                  # noqa: E402
from dq11tool import grammar, grammar_asm                # noqa: E402
from dq11tool.textcodec import GRAMMAR, N_END, N_MID, N_START, S_START, encode_str  # noqa: E402

BASE = 0x724400
BUF = 0x10000100


def run_arm(sim, text: str, cap: int) -> str:
    raw = text.encode("utf-16-le") + b"\0\0"
    assert len(raw) <= cap * 2 + 2
    sim.mem[BUF - sim.mem_base:BUF - sim.mem_base + cap * 2 + 64] = bytes(cap * 2 + 64)
    sim.mem[BUF - sim.mem_base:BUF - sim.mem_base + len(raw)] = raw
    sim.r = [0x11110000 + k for k in range(16)]
    keep = list(sim.r[4:12])
    sim.call(BASE, BUF, cap)
    assert sim.r[4:12] == keep, "callee-saved registers changed"
    out = bytes(sim.mem[BUF - sim.mem_base:BUF - sim.mem_base + cap * 2])
    return out.decode("utf-16-le").split("\0")[0]


CASES = [
    ("{The}{noun}Slime takes 1 {n:point|points} of damage!", 256),
    ("{The}Gemma takes 12 {n:point|points} of damage!", 256),
    ("{A}{noun}Iron turtle appears!", 256),
    ("A group of {noun}Dracky{pl} appears!", 256),
    ("Hero attacks {the}{noun}She-slime B!", 256),
    ("{noun}Slime appears!", 256),
    ("Hero receives {n:{a}{noun}Medicinal herb|3 {noun}Medicinal herb{pl}}!", 256),
    ("Hero receives {n:{a}{noun}Medicinal herb|1 {noun}Medicinal herb{pl}}!", 256),
    ("Defeated {noun}Fox{pl}: {noun}Box{pl} {noun}Witch{pl} {noun}Bush{pl} {noun}Day{pl}", 256),
    ("Gains 10 {n:point|points}. Hero! {noun}Slime here. Big {noun}Slime.", 256),
    ("{n:point|points} 1", 256),
    ("{The}{noun}Slime{pl}{grp} {s:is|are} defeated.", 256),
    ("{The}{noun}Slime {s:is|are} defeated.", 256),
    ("{The}{noun}Dracky{pl}{grp} {s:takes|take} 1 {n:point|points}.", 256),
    ("{n:one|more} and 21", 256),
    ("broken {n:x|y", 256),
    ("{the}", 256),
    ("{A}", 256),
    ("x{pl}", 256),
    ("{pl}", 256),
    ("{The}{noun}Slime takes 1 {n:point|points} of damage!", 20),
    ("{the}{noun}Slime {the}{noun}Slime {the}{noun}Slime", 30),
]

PIECES = ["{the}", "{The}", "{a}", "{A}", "{pl}", "{noun}", "{n:", "{s:", "{grp}", "|", "}", "1", "12", " ", "!", ". ",
          "Slime", "orc", "Ax", "y", "ey", "h", "ch", "\n", "Hero", "a"]


def main(n_random=3000):
    sim = Sim(grammar_asm.assemble(BASE), BASE)
    bad = 0
    tests = [(encode_str(t), cap) for t, cap in CASES]
    rnd = random.Random(7)
    for _ in range(n_random):
        t = "".join(rnd.choice(PIECES) for _ in range(rnd.randint(1, 25)))
        # random soup: markers as raw characters (also unbalanced {n: ... })
        raw = t
        for tag, ch in list(GRAMMAR.items()) + [("n:", N_START), ("s:", S_START)]:
            raw = raw.replace("{" + tag + ("" if tag.endswith(":") else "}"), ch)
        raw = raw.replace("|", N_MID).replace("}", N_END)
        tests.append((raw, rnd.choice([256, 256, 40, 12])))
    for raw, cap in tests:
        raw = raw[:cap - 1]
        want = grammar.resolve(raw, cap - 1)
        try:
            got = run_arm(sim, raw, cap)
        except RuntimeError as e:
            print("HANG", repr(raw), cap, e)
            return 1
        if got != want:
            bad += 1
            if bad <= 15:
                print("MISMATCH cap", cap)
                print("  in  ", repr(raw))
                print("  want", repr(want))
                print("  got ", repr(got))
    print(f"{len(tests) - bad}/{len(tests)} match, code {len(sim.code)} bytes, {sim.steps} steps")
    for t, cap in CASES[:10]:
        print(" ", grammar.resolve(encode_str(t), cap - 1))
    return bad


if __name__ == "__main__":
    sys.exit(1 if main(*(int(a) for a in sys.argv[1:2])) else 0)
