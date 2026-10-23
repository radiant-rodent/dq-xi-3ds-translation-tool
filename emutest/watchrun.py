"""watchrun.py ADDR[:SIZE[:KIND]] "SEQ BEFORE" "SEQ TRIGGER" - boot the test build with the GDB
stub, play SEQ BEFORE (comma separated azctl-style steps: A, walkU:0.4, wait1.5), set a
watchpoint (KIND 2 write / 3 read / 4 access / 0 breakpoint), play SEQ TRIGGER and print the
registers and stack of the first hits."""
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az  # noqa: E402
from gdb import Gdb  # noqa: E402


def play(e, seq):
    for b in filter(None, seq.split(",")):
        if b.startswith("wait"):
            e.wait_game(e.game_time() + float(b[4:]))
        elif b.startswith("walk"):
            e.walk(b[4:].split(":")[0], float(b.split(":")[1]))
        else:
            e.press(b, after=0.6)


def find_panes(e, name: str, lo=0x33000000, hi=0x34000000) -> list[int]:
    """Layout pane objects by name (the name is stored at pane + 0x80)."""
    pat, out = name.encode() + b"\0", []
    for a in range(lo, hi, 0x10000):
        d = b"".join(e.read(b, 0x1000) for b in range(a, a + 0x10000, 0x1000))
        i = d.find(pat)
        while i >= 0:
            if i % 4 == 0:
                out.append(a + i - 0x80)
            i = d.find(pat, i + 1)
    return out


def find_u16(e, text: str, lo=0x33000000, hi=0x34000000) -> list[int]:
    pat, out = text.encode("utf-16-le"), []
    for a in range(lo, hi, 0x10000):
        d = b"".join(e.read(b, 0x1000) for b in range(a, a + 0x10000, 0x1000))
        i = d.find(pat)
        while i >= 0:
            out.append(a + i)
            i = d.find(pat, i + 1)
    return out


def main(spec, before, trigger, hits=3, boot_until=70, save="user_citra", realtime=False):
    """spec: ADDR[:SIZE[:KIND]] or pane:NAME:OFFSET (every pane of that name, e.g.
    pane:null_scroll_talk:0x20 = its translate y)."""
    parts = spec.split(":")
    e = az.Azahar(az.JP_ROM, save=save, workdir=os.path.join(az.ROOT, "runs", "gdb"), settings=dict(az.GDB, **({"frame_limit": "100"} if realtime else {})))
    # the stub halts the CPU until a debugger attaches: connect while start() waits for the process
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("g", Gdb()))
    t.start()
    try:
        e.start()
    finally:
        t.join()
    g = box["g"]
    g.cont()
    try:
        e.wait_game(6)
        while e.game_time() < boot_until:
            e.press("A" if e.game_time() < 40 else "B", after=0.4)
        play(e, before)
        if parts[0] == "u16":           # halt first: the text may change while we search
            print("interrupt:", g.interrupt())
            addrs = find_u16(e, parts[1])
            size, kind = 2, 2
            for addr in addrs:
                print(f"watch {addr:08x}:", g.watch(addr, size, kind))
        elif parts[0] == "pane":
            addrs = [p + int(parts[2], 16) for p in find_panes(e, parts[1])]
            size, kind = 4, 2
        else:
            addrs = [int(parts[0], 16)]
            size = int(parts[1]) if len(parts) > 1 else 4
            kind = int(parts[2]) if len(parts) > 2 else 2
        if parts[0] != "u16":
            print("interrupt:", g.interrupt())
            for addr in addrs:
                print(f"watch {addr:08x}:", g.watch(addr, size, kind))
        g.cont()
        th = threading.Thread(target=lambda: play(e, trigger), daemon=True)
        th.start()
        for n in range(hits):
            stop = g.wait(120)
            if stop is None:
                print("no hit")
                break
            r = g.regs()
            print(f"hit {n}: {stop[:40]}")
            print("  " + " ".join(f"{k}={v:08x}" for k, v in r.items()))
            st = g.stack(160)
            print("  code ptrs on stack:", " ".join(f"{v:08x}" for v in st if 0x100000 <= v < 0x725000))
            g.cont()
        for addr in addrs:
            g.unwatch(addr, size, kind)
    finally:
        g.close()
        e.stop()


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--realtime"]
    main(a[0], a[1], a[2], int(a[3]) if len(a) > 3 else 3, realtime="--realtime" in sys.argv)
