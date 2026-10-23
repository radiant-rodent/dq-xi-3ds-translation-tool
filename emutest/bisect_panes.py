"""bisect_panes.py PACK MEMBER - which text panes of one menu-pack layout corrupt the heap
when their buffers grow x2 (see bisect_layouts.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az, build_test                                   # noqa: E402
from bisect_layouts import ROM                          # noqa: E402
from dq11tool.bflyt import Bflyt, numeric_text          # noqa: E402
from dq11tool.darc import Sarc                          # noqa: E402
from dq11tool.pack import Pack                          # noqa: E402

PACK, MEMBER = sys.argv[1], sys.argv[2]


def panes():
    p = Pack.parse(open(os.path.join(az.REPO, "workspace", "extracted", "romfs", "ui", PACK), "rb").read())
    for e in p.entries:
        if e.data[:4] == b"SARC":
            s = Sarc.parse(e.data)
            for n in s.nodes:
                if n.name == MEMBER:
                    lay = Bflyt(s.raw[s.data_offset + n.start:s.data_offset + n.end])
                    return sorted({t.name for t in lay.text_panes() if not numeric_text(t.text)})
    raise SystemExit("layout not found")


def crashes(names) -> bool:
    build_test.build(menu_buffer_factor=2.0, expand_only=frozenset({(PACK, MEMBER)}),
                     expand_panes=frozenset(names))
    with az.Azahar(ROM, save="user_citra", workdir=os.path.join(az.ROOT, "runs", "bisect")) as e:
        e.wait_game(6)
        while e.game_time() < 75:
            if e.crashed():
                return True
            e.press("A" if e.game_time() < 40 else "B", after=1.0)
        for b in ("X", "DOWN", "A"):
            e.press(b, after=1.0)
        e.wait_game(e.game_time() + 3)
        return bool(e.crashed())


if __name__ == "__main__":
    cand = panes()
    print(len(cand), "panes", flush=True)
    if not crashes(cand):
        print("no crash with all of them"); sys.exit()
    while len(cand) > 1:
        half = cand[:len(cand) // 2]
        c = crashes(half)
        print("first half", len(half), "crash" if c else "ok", half if len(half) < 6 else "", flush=True)
        if c:
            cand = half
        else:
            rest = cand[len(cand) // 2:]
            if not crashes(rest):
                print("neither half crashes alone: cumulative", cand); sys.exit()
            cand = rest
    print("culprit:", cand)
