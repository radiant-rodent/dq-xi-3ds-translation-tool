"""Temporary translation overrides for test builds: with tempdb.overrides({source_id: text}):
build_test.build(...). The original rows are restored afterwards (also on errors)."""
import contextlib
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az  # noqa: E402

DB = os.path.join(az.REPO, "workspace", "project.db")


@contextlib.contextmanager
def overrides(texts: dict):
    db = sqlite3.connect(DB, timeout=30)
    saved = {sid: db.execute("SELECT translation, status FROM source WHERE id=?", (sid,)).fetchone() for sid in texts}
    try:
        for sid, t in texts.items():
            db.execute("UPDATE source SET translation=?, status=2 WHERE id=?", (t, sid))
        db.commit()
        yield
    finally:
        for sid, (t, st) in saved.items():
            db.execute("UPDATE source SET translation=?, status=? WHERE id=?", (t, st, sid))
        db.commit()
        db.close()
