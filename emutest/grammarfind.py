"""grammarfind.py - find the code that expands variables ({v:N}) into battle messages, for a
runtime grammar pass (articles, plurals).

Same setup as wrapfind.py (long test name for the slime, GDB stub, battlego's route into the
battle). In the battle: every UTF-16 copy of the name is either a stored name (followed by NUL)
or a finished message ("... appears!"). Read watchpoints on the stored names and a write
watchpoint on the message buffer, then A presses: the next message is expanded into the same
buffer and the name is read while it is copied in. Prints pc/lr/registers and code pointers on
the stack per hit. Output: _emutest/runs/grammarfind/hits.json.

    python emutest/grammarfind.py
    python emutest/wrapfind.py --restore   # after a failed run
"""
import json
import os
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az            # noqa: E402
import azctl         # noqa: E402
import battlego      # noqa: E402
import build_test    # noqa: E402
import wrapfind      # noqa: E402
from gdb import Gdb  # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "grammarfind")


LABEL_PCS = {0x6EFC3C, 0x6EFF50, 0x6F01CC, 0x6F05F4, 0x63A57C, 0x63A570, 0x53FA58}


def hunt(s, g):
    """Read watchpoints on every stored copy of the name from the start of the battle, A
    presses through the first attack; hits outside the label redraw (LABEL_PCS) are printed."""
    import time
    name16 = wrapfind.NAME.encode("utf-16-le")
    stored = wrapfind.find_all(s, name16)
    print("copies:", [hex(h) for h in stored], flush=True)
    print("interrupt:", g.interrupt()[:20], flush=True)
    watches = [(h & ~3, 3) for h in stored]
    for a, k in watches:
        print(f"watch {a:08x} kind {k}:", g.watch(a, 4, k), flush=True)
    g.cont()
    th = threading.Thread(target=lambda: [s.press("A", after=2.5) for _ in range(8)], daemon=True)
    th.start()
    seen = {}
    t_end = time.time() + 600
    n = 0
    while time.time() < t_end and len(seen) < 25:
        stop = g.wait(30)
        if stop is None:
            print("no more hits")
            break
        n += 1
        r = g.regs()
        key = (r["pc"], r["lr"])
        if key not in seen and r["pc"] not in LABEL_PCS:
            st = g.stack(200)
            seen[key] = {"regs": {k: f"{v:08x}" for k, v in r.items()},
                         "stack": [f"{v:08x}" for v in st if 0x100000 <= v < 0x725000]}
            print(f"hit {n} ({stop[:12]}): pc={r['pc']:08x} lr={r['lr']:08x} "
                  + " ".join(f"{k}={v:08x}" for k, v in r.items() if k not in ("pc", "lr")))
            print("  code ptrs:", " ".join(seen[key]["stack"]), flush=True)
        g.cont()
    print("total hits", n, flush=True)
    g.interrupt()
    for a, k in watches:
        g.unwatch(a, 4, k)
    g.cont()
    json.dump({f"{k[0]:08x}/{k[1]:08x}": v for k, v in seen.items()},
              open(os.path.join(OUT, "hits.json"), "w"), indent=1)


def main():
    os.makedirs(OUT, exist_ok=True)
    db = sqlite3.connect(battlego.DB)
    orig = db.execute("SELECT translation FROM source WHERE id=?", (battlego.SLIME,)).fetchone()[0]
    assert orig == "Slime", orig
    g = None
    try:
        db.execute("UPDATE source SET translation=? WHERE id=?", (wrapfind.NAME, battlego.SLIME))
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
        wrapfind.restore(db, g)


if __name__ == "__main__":
    main()
