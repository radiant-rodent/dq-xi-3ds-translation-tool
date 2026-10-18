"""squeezes.py - glyph squeeze factor and widest text of every export.RUNTIME_FIT pane, with
the current translations (no build needed)."""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dq11tool import export, gamefont  # noqa: E402
from dq11tool.bflyt import Bflyt  # noqa: E402
from dq11tool.pack import Pack  # noqa: E402
from dq11tool.project import Project  # noqa: E402
from dq11tool.darc import Sarc  # noqa: E402

p = Project("workspace")
db = p.db()
rows = [dict(kind=k, entry=e, src=s, tr=t, sid=i) for k, e, s, t, i in db.execute(
    "SELECT e.kind, e.entry, s.text, s.translation, s.id FROM entry e JOIN source s ON s.id = e.source_id"
    " WHERE s.translation IS NOT NULL AND s.translation != ''")]
names = export.runtime_names(rows)
fonts = gamefont.for_project(p)._fonts()
want = {n for n, *_ in export.RUNTIME_FIT}
for f in glob.glob("workspace/extracted/romfs/ui/*.pack"):
    pack = Pack.parse(open(f, "rb").read())
    for e in pack.entries:
        if e.data[:4] != b"SARC":
            continue
        sarc = Sarc.parse(e.data)
        for n in sarc.nodes:
            base = os.path.basename(n.name).rsplit(".", 1)[0]
            if base not in want:
                continue
            lay = Bflyt(sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end])
            export.runtime_fit(lay, base, names, fonts)
            print(os.path.basename(f), base, {k: round(v, 2) for k, v in lay.squeeze_names.items()},
                  {k: v for k, v in lay.width_to.items()},
                  {k: f"OVERFLOW {v:.0f}px" for k, v in lay.runtime_overflow.items()})
