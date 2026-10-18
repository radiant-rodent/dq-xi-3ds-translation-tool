"""profrun.py LABEL OUT.json - boot the current mod (oom probe) until t=45 (save loaded, field
loading) and dump the per-caller profile of allocations that reached the UI fallback heap."""
import sys, os, struct, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az
rom = az.JP_ROM
with az.Azahar(rom, save="user_citra", workdir=os.path.join(az.ROOT, "runs", "heap")) as e:
    e.wait_game(6)
    while e.game_time() < 45 and not e.crashed():
        e.press("A" if e.game_time() < 40 else "B", after=0.5)
    tab = struct.unpack("<96I", e.read(0x839D80, 384))
    rows = [(tab[3*i], tab[3*i+1], tab[3*i+2]) for i in range(32) if tab[3*i]]
    json.dump(rows, open(sys.argv[2], "w"))
    print(sys.argv[1], "crash:", e.crashed())
    for lr, tot, n in sorted(rows, key=lambda r: -r[1])[:20]:
        print(f"   lr {lr:08x} total {tot:>9} count {n:>6}")
