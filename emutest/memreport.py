"""memreport.py LABEL [SCENARIO] - boot the current mod (needs testhook.HEAP_LIVE + oom probes),
play a scenario and print every heap's size, live bytes, high-water mark and worst headroom.

SCENARIO: comma separated presses after boot (default: camp menu, bag, item details, equip).
The peak includes block headers but not fragmentation, so keep a margin."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az  # noqa: E402

HEAPS, LIVE, OOM = 0x839D00, 0x839900, 0x839C80
NAMES = {0x7F4FEC: "main", 0x7F5144: "ui", 0x7F4F5C: "10.6MB", 0x7F501C: "3MB", 0x7F504C: "1.75MB",
         0x7F4F2C: "heap0", 0x7F4F8C: "256KB", 0x7F4FBC: "160KB", 0x7F50C8: "ui1-fb"}
DEFAULT = "X,DOWN,A,A,B,B,B,X,DOWN,DOWN,A,A,Y,B,B,B,B"


def report(read):
    tab = struct.unpack("<32I", read(HEAPS, 128))
    live = struct.unpack("<32i", read(LIVE, 128))
    rows = []
    for i in range(16):
        heap = tab[2 * i]
        if not heap:
            break
        size = struct.unpack("<I", read(heap + 4, 4))[0]
        rows.append((NAMES.get(heap, f"{heap:08x}"), size, live[2 * i], live[2 * i + 1], tab[2 * i + 1]))
    return rows


def run(label, seq=DEFAULT, boot_until=70, save="user_citra", shots=None, movie=None):
    rom = az.JP_ROM
    with az.Azahar(rom, movie=movie, save=save, workdir=os.path.join(az.ROOT, "runs", "heap")) as e:
        e.wait_game(6)
        while movie is None and e.game_time() < boot_until and not e.crashed():
            e.press("A" if e.game_time() < 40 else "B", after=0.4)
        for b in filter(None, seq.split(",")):
            if e.crashed():
                break
            if b.startswith("wait"):
                e.wait_game(e.game_time() + float(b[4:]))
            elif b.startswith("walk"):
                e.walk(b[4:].split(":")[0], float(b.split(":")[1]))
            elif b == "shot" and shots:
                e.shot(os.path.join(shots, f"m{e.game_time():07.1f}.png"))
            else:
                e.press(b, after=0.6)
        c, lr, size = struct.unpack("<3I", e.read(OOM, 12))
        print(f"{label}: OOM {c}" + (f" (last caller {lr:08x}, {size} bytes)" if c else ""),
              "crash:", e.crashed())
        for name, size, live, peak, maxreq in report(e.read):
            print(f"   {name:8} size {size:>9}  live {live:>9}  peak {peak:>9}  headroom {size - peak:>9}"
                  f"  largest request {maxreq}")


if __name__ == "__main__":
    a = sys.argv
    run(a[1], a[2] if len(a) > 2 else DEFAULT, save=a[3] if len(a) > 3 else "user_citra",
        shots=a[4] if len(a) > 4 else None)
