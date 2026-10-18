"""wrapfind.py - find the code that breaks runtime text (battle messages) into lines; it breaks
inside words ("...Very Lon / Name is defeated.").

Test build with a very long one-line slime name, boot cobblestone_start with the GDB stub (the
connection stays open the whole run: re-attaching after a detach does not work), battlego's
route into the slime battle, then A until a finished message with the name is in RAM (the
strings are whole, without line breaks: the break is made while laying the text out). A read
watchpoint inside "Very Long" then stops in the code that reads the text for layout.
Restores the slime name and the normal build at the end. Output: _emutest/runs/wrapfind/.

    python emutest/wrapfind.py            # the run
    python emutest/wrapfind.py --restore  # slime name + normal build back (after a failed run)
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
from gdb import Gdb  # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "wrapfind")
NAME = "Malicious Abominable Showman of the Very Long Name"
RANGES = [(0x08000000, 0x10000000), (0x14000000, 0x18000000), (0x30000000, 0x38000000)]
NUL = "\x00"


def find_all(s, pat: bytes):
    hits = []
    for lo, hi in RANGES:
        for a in range(lo, hi, 0x40000):
            try:
                d = b"".join(s.read(b, 0x1000) for b in range(a, a + 0x40000, 0x1000))
            except Exception:
                continue
            i = d.find(pat)
            while i >= 0:
                if i % 2 == 0:
                    hits.append(a + i)
                i = d.find(pat, i + 1)
    return hits


def find_all_bytes(s, pat: bytes):
    hits = []
    for lo, hi in RANGES:
        for a in range(lo, hi, 0x40000):
            try:
                d = b"".join(s.read(b, 0x1000) for b in range(a, a + 0x40000, 0x1000))
            except Exception:
                continue
            i = d.find(pat)
            while i >= 0:
                hits.append(a + i)
                i = d.find(pat, i + 1)
    return hits


def message_with_name(s):
    """(address, text after the name) of a finished message that contains the test name."""
    name16 = NAME.encode("utf-16-le")
    for h in find_all(s, name16):
        after = s.read(h + len(name16), 40).decode("utf-16-le", "replace").split(NUL)[0]
        if len(after) > 3:
            return h, after
    return None


FONT_DIR = os.path.join(az.USER, "load", "mods", "0004000000199200", "romfs", "font")


def width_entry(font_file: str, ch: str) -> int:
    """File offset of the CWDH width entry (3 bytes) of character ch."""
    import struct
    from dq11tool.fontimport import Font
    data = open(font_file, "rb").read()
    f = Font(data)
    gi = f.cmap[ord(ch)]
    q = struct.unpack_from("<I", data, f.finf + (20 if data[:4] == b"CFNT" else 0x18))[0]
    while q:
        b = q - 8
        s0, e0, nx = struct.unpack_from("<HHI", data, b + 8)
        if s0 <= gi <= e0:
            return b + 16 + (gi - s0) * 3
        q = nx
    raise KeyError(ch)


def hunt(s, g):
    """Read watchpoints on the width entry of 'V' (only in the test name) in the loaded message
    fonts, then on through the battle: the layout code reads the width of every character."""
    spots = []
    for fn in ("3D14", "3D12"):
        path = os.path.join(FONT_DIR, fn + ".bffnt")
        data = open(path, "rb").read()
        off = width_entry(path, "V")
        # the header is relocated at load (offsets -> pointers); the width table is not
        pat = data[off - 30:off + 30]
        found = [h for h in find_all_bytes(s, pat)]
        print(fn, "V width entries in RAM:", [hex(h + 30) for h in found], flush=True)
        spots += [h + 30 for h in found]
    if not spots:
        print("fonts not found in RAM")
        return
    print("interrupt:", g.interrupt()[:20], flush=True)
    for sp in spots:
        print(f"read watch {sp:08x}:", g.watch(sp & ~3, 4, 3), flush=True)
    g.cont()
    th = threading.Thread(target=lambda: [s.press("A", after=1.2) for _ in range(12)], daemon=True)
    th.start()
    seen = {}
    for n in range(40):
        stop = g.wait(20)
        if stop is None:
            print("no more hits")
            break
        r = g.regs()
        key = (r["pc"], r["lr"])
        if key not in seen:
            st = g.stack(160)
            seen[key] = [v for v in st if 0x100000 <= v < 0x725000]
            print(f"hit {n}: pc={r['pc']:08x} lr={r['lr']:08x} "
                  + " ".join(f"{k}={v:08x}" for k, v in r.items() if k not in ("pc", "lr")))
            print("  code ptrs:", " ".join(f"{v:08x}" for v in seen[key]), flush=True)
        g.cont()
    g.interrupt()
    for sp in spots:
        g.unwatch(sp & ~3, 4, 3)
    g.cont()
    json.dump({f"{k[0]:08x}/{k[1]:08x}": [f"{v:08x}" for v in vs] for k, vs in seen.items()},
              open(os.path.join(OUT, "hits.json"), "w"), indent=1)


def main():
    os.makedirs(OUT, exist_ok=True)
    db = sqlite3.connect(battlego.DB)
    orig = db.execute("SELECT translation FROM source WHERE id=?", (battlego.SLIME,)).fetchone()[0]
    assert orig == "Slime", orig
    g = None
    try:
        db.execute("UPDATE source SET translation=? WHERE id=?", (NAME, battlego.SLIME))
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
        restore(db, g)


def restore(db=None, g=None):
    db = db or sqlite3.connect(battlego.DB)
    db.execute("UPDATE source SET translation='Slime' WHERE id=?", (battlego.SLIME,))
    db.commit()
    if g:
        g.close()
    azctl.main(["stop"])
    build_test.build()


if __name__ == "__main__":
    restore() if "--restore" in sys.argv else main()
