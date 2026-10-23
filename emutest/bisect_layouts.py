"""bisect_layouts.py - which menu-pack layouts corrupt the heap when their text buffers grow x2.
Each step builds a mod where only a subset of the (pack, layout) pairs of the menu packs get
the x2 buffers, boots to the field and watches for the heap-corruption crash."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az, build_test
from dq11tool.pack import Pack
from dq11tool.darc import Sarc
from dq11tool.export import MENU_PACKS

REPO = az.REPO
ROM = az.JP_ROM
LOG = os.path.join(az.ROOT, "runs", "bisect.json")


def members():
    out = []
    for f in MENU_PACKS:
        p = Pack.parse(open(os.path.join(az.REPO, "workspace", "extracted", "romfs", "ui", f), "rb").read())
        for e in p.entries:
            if e.data[:4] == b"SARC":
                s = Sarc.parse(e.data)
                for n in s.nodes:
                    if s.raw[s.data_offset + n.start:s.data_offset + n.start + 4] == b"FLYT":
                        out.append((f, n.name))
    return sorted(set(out))


def crashes(subset) -> bool:
    build_test.build(menu_buffer_factor=2.0, expand_only=frozenset(subset))
    with az.Azahar(ROM, save="user_citra", workdir=os.path.join(az.ROOT, "runs", "bisect")) as e:
        e.wait_game(6)
        while e.game_time() < 75:
            if e.crashed():
                return True
            e.press("A" if e.game_time() < 40 else "B", after=1.0)
        # open the camp menu and the bag, where the original crash was seen
        for b in ("X", "DOWN", "A"):
            e.press(b, after=1.0)
        e.wait_game(e.game_time() + 3)
        return bool(e.crashed())


if __name__ == "__main__":
    cand = members()
    print(len(cand), "layouts")
    hist = []
    if not crashes(cand):
        print("no crash with all of them"); sys.exit()
    while len(cand) > 1:
        half = cand[:len(cand) // 2]
        c = crashes(half)
        hist.append((len(half), c))
        print("first half", len(half), "crash" if c else "ok", flush=True)
        cand = half if c else cand[len(cand) // 2:]
        json.dump({"left": cand, "hist": hist}, open(LOG, "w"))
    print("culprit:", cand)
