"""grammartop.py [LABEL] - the grammar pass on the top-screen battle line.

The first battle's top-screen lines carry no monster names, so a test build gives the line
that comes with every attack (source 22, "{The}{v:2} attacks!") grammar markers with a literal
{noun} name. Resolved, it reads "Hero attacks the orc for 1 point!" (TEST_WANT); unresolved
the markers are invisible and it reads "Hero attacks Orc for 1 pointpoints!".
battlego's fight with --dump style frames; the line and the normal build are restored after.
Screenshots: _emutest/runs/battlego/LABEL/.
"""
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az            # noqa: E402
import azctl         # noqa: E402
import battlego      # noqa: E402
import build_test    # noqa: E402

LINE = 22
TEST = "{The}{v:2} attacks {the}{noun}Orc for 1 {n:point|points}!"
TEST_WANT = "attacks the orc for 1 point!"
NEEDLE = "attacks"


def main(label="grammartop"):
    out = os.path.join(az.ROOT, "runs", "battlego", label)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*.png")) + glob.glob(os.path.join(out, "strings.txt")):
        os.remove(f)
    battlego.DUMP = True
    db = sqlite3.connect(battlego.DB)
    orig = db.execute("SELECT translation FROM source WHERE id=?", (LINE,)).fetchone()[0]
    assert "Orc" not in orig, orig
    try:
        db.execute("UPDATE source SET translation=? WHERE id=?", (TEST, LINE))
        db.commit()
        build_test.build()
        s = battlego.fight(out, until_battle=True)
        if s is None:
            print("no battle")
            return
        for i in range(30):                     # Attack every turn; Gemma cheers early on
            s.press("A", after=0.6)
            for k in range(3):
                s.wait_game(s.game_time() + 0.4)
                s.shot(os.path.join(out, f"t{i:02d}_{k}.png"))
            battlego.dump_strings(s, out, f"t{i:02d}", needle=NEEDLE)
            if abs(battlego.walkto.pos(s)[0]) > 0.5:
                break
        print("crashed", s.crashed())
        hits = [l for l in open(os.path.join(out, "strings.txt"), encoding="utf-8") if NEEDLE in l]
        print("RAM:", sorted(set(l[10:].strip() for l in hits))[:6])
        print("resolved in RAM:", any(TEST_WANT in l for l in hits))
    finally:
        db.execute("UPDATE source SET translation=? WHERE id=?", (orig, LINE))
        db.commit()
        azctl.main(["stop"])
        build_test.build()


if __name__ == "__main__":
    main(*sys.argv[1:2])
