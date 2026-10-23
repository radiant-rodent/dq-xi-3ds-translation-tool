"""bisect_entries.py PACK - which entries of one translated pack drive the fallback heap
(0x7F5144) to exhaustion. Each step writes a mod with only that pack, where only a subset of
its entries carries the translation (the rest are the original Japanese entries), boots and
measures the heap's smallest free block (heapmin metric)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az, build_test, heaps                                # noqa: E402
from dq11tool import testhook                               # noqa: E402
from dq11tool.export import ExportOptions, collect_changes  # noqa: E402
from dq11tool.pack import Pack                              # noqa: E402
from dq11tool.project import Project                        # noqa: E402

PACK = sys.argv[1]                       # e.g. /fieldcmn.pack
LIMIT = 300000                           # "bad" when the smallest free block drops below this
ROM = az.JP_ROM

orig = Pack.parse(open(os.path.join(build_test.REPO, "workspace", "extracted", "romfs", PACK.lstrip("/")), "rb").read())
romfs, _, _, code = collect_changes(Project(os.path.join(build_test.REPO, "workspace")),
                                    ExportOptions(make_3ds=False, make_layeredfs=False, test_input_hook=True,
                                                  test_probes=("heap_track",)))
new = Pack.parse(romfs[PACK])
changed = [e.name for e, o in zip(new.entries, orig.entries) if e.data != o.data]


def min_free(subset) -> int:
    mixed = Pack.parse(romfs[PACK])
    for e, o in zip(mixed.entries, orig.entries):
        if e.name not in subset:
            e.data = o.data
    build_test.write_mod({PACK: mixed.build()}, code)
    low = 1 << 30
    with az.Azahar(ROM, save="user_citra", workdir=os.path.join(az.ROOT, "runs", "heap")) as e:
        e.wait_game(6)
        while e.game_time() < 70 and not e.crashed():
            e.press("A" if e.game_time() < 40 else "B", after=0.2)
            try:
                low = min(low, heaps.walk(e.read, 0x7F5144)[1])
            except Exception:
                pass
    return low


if __name__ == "__main__":
    print(len(changed), "changed entries:", changed, flush=True)
    cand = changed
    while len(cand) > 1:
        half = cand[:len(cand) // 2]
        m = min_free(half)
        print("first half", len(half), "min free", m, half if len(half) < 8 else "", flush=True)
        if m < LIMIT:
            cand = half
        else:
            rest = cand[len(cand) // 2:]
            m2 = min_free(rest)
            print("second half", len(rest), "min free", m2, flush=True)
            if m2 >= LIMIT:
                print("neither half alone: cumulative", cand); sys.exit()
            cand = rest
    print("culprit:", cand, "min free alone", min_free(cand))
