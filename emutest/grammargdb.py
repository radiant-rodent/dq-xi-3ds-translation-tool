"""grammargdb.py - which texts reach the grammar pass in battle, and how (GDB).

Test build: source 22 ("{The}{v:2} attacks!", the top-screen line of every attack) gets
grammartop.TEST (a literal {noun} name and {n:..}). GDB stub boot of cobblestone_start,
battlego's route into the slime battle, then a read watchpoint on the article strings in the
grammar cave's data: gfix reads them only when it writes an article. Per hit: the buffer being
resolved (gfix's r4), its pane (stub: [sp + ...]) and the code pointers on the stack.
Shows whether the top line is typed out through repeated partial SetString calls.
Restores the line and the normal build. Output: _emutest/runs/grammargdb/.
"""
import json
import os
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import az            # noqa: E402
import azctl         # noqa: E402
import battlego      # noqa: E402
import build_test    # noqa: E402
import grammartop    # noqa: E402
from gdb import Gdb  # noqa: E402
from dq11tool import grammar_asm, grammar_code  # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "grammargdb")
DATA_AT = grammar_code.CAVE + len(grammar_code.CODE) - len(grammar_asm.DATA)


def u16(g, a, n=120):
    raw = g.read(a, n * 2)
    return raw.decode("utf-16-le", "replace").split("\0")[0]


def hunt(s, g):
    print("interrupt:", g.interrupt()[:20], flush=True)
    print(f"watch {DATA_AT:08x}:", g.watch(DATA_AT, 8, 3), flush=True)
    g.cont()

    def presses():
        for _ in range(14):
            s.press("A", after=1.6)
    th = threading.Thread(target=presses, daemon=True)
    th.start()
    hits = []
    for n in range(80):
        stop = g.wait(25)
        if stop is None:
            print("no more hits")
            break
        r = g.regs()
        buf = r["r4"]
        text = u16(g, buf)
        st = [f"{v:08x}" for v in g.stack(120) if 0x100000 <= v < 0x725000]
        hits.append({"pc": f"{r['pc']:08x}", "buf": f"{buf:08x}", "text": text, "stack": st[:12]})
        print(f"hit {n}: buf={buf:08x} {text!r}  stack {' '.join(st[:8])}", flush=True)
        g.cont()
    g.interrupt()
    g.unwatch(DATA_AT, 8, 3)
    g.cont()
    json.dump(hits, open(os.path.join(OUT, "hits.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def main():
    os.makedirs(OUT, exist_ok=True)
    db = sqlite3.connect(battlego.DB)
    line = grammartop.LINE
    orig = db.execute("SELECT translation FROM source WHERE id=?", (line,)).fetchone()[0]
    assert "Orc" not in orig, orig
    g = None
    try:
        db.execute("UPDATE source SET translation=? WHERE id=?", (grammartop.TEST, line))
        db.commit()
        build_test.build()
        e = az.Azahar(az.JP_ROM, save="cobblestone_start", workdir=OUT, settings=dict(az.GDB))
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("g", Gdb()))
        th.start()
        try:
            e.start()
        finally:
            th.join()
        g = box["g"]
        g.cont()
        json.dump({"pid": e.proc.pid, "rom": az.JP_ROM}, open(azctl.STATE, "w"))
        s = battlego.fight(OUT, start=False, until_battle=True)
        if s is None:
            print("no battle")
            return
        hunt(s, g)
    finally:
        db.execute("UPDATE source SET translation=? WHERE id=?", (orig, line))
        db.commit()
        if g:
            g.close()
        azctl.main(["stop"])
        build_test.build()


if __name__ == "__main__":
    main()
