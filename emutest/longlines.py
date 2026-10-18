"""longlines.py - message-window strings whose official PC line is still too long: count by
table (entry label / bxon) and file."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dq11tool import gamefont  # noqa: E402
from dq11tool.project import Project  # noqa: E402
from dq11tool.textcodec import encode_str  # noqa: E402

p = Project("workspace")
gf = gamefont.for_project(p)
db = p.db()
rows = db.execute("SELECT s.id, s.text, s.translation, m.en FROM source s JOIN pc_match m ON m.source_id = s.id"
                  " AND m.rank = 0 AND m.kind = 'exact' WHERE s.translation IS NOT NULL").fetchall()
by, files, ids = Counter(), Counter(), []
for sid, src, tr, en in rows:
    if not gf.paged(sid):
        continue
    new = gf.wrap(sid, src, en)
    if new == tr or (gf.fits(sid, src, new) and len(encode_str(new.split("{PAGE}")[0])) <= 255):
        continue
    ids.append(sid)
    for lab, ent, f in db.execute("SELECT label, entry, file FROM entry WHERE source_id=?", (sid,)):
        by[(lab, ent)] += 1
        files[f.split("/")[1] if f.count("/") > 1 else f] += 1
print(len(ids), "too long")
for k, v in by.most_common(25):
    print(v, k)
print(files.most_common(10))
