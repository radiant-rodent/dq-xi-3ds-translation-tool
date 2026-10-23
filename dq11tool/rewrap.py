"""Re-flow translations that were line-wrapped for the original (Japanese) fonts.

Translations are wrapped automatically when they are drafted or imported. Those breaks were
measured with the game's own wide Latin glyphs; with the narrow DQ VII glyphs (fontimport)
they leave lines half empty. A translation is re-wrapped only when its line breaks are
exactly what the wrapper produced with the original font, so breaks set by hand stay.

    python -m dq11tool.rewrap [--workdir workspace] [--apply]
    python -m dq11tool.rewrap --overflow [--apply]   # texts whose breaks no longer fit

--overflow: translations whose current line breaks overflow a window the string is shown in
(gamefont.overshoot, e.g. after a new limit such as gamefont.MESSAGE_2D) and that fit when
re-wrapped. Only the breaks change; the words stay (also for reviewed text).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from collections import Counter

from . import boxfit, gamefont
from .bffnt import Bffnt
from .ctr.formats import open_image
from .fontpatch import patch_font
from .metrics import wrap


def _original_font(project, name: str = "3D14") -> Bffnt:
    """The font the existing breaks were measured with: the game's own, width fixes only."""
    f, _, ncch = open_image(project.rom_path)
    try:
        r = ncch.romfs()
        raw = r.read_file(next(x for x in r.files if x.path == f"/font/{name}.bffnt"))
    finally:
        f.close()
    return Bffnt.parse(patch_font(raw))


def plan(project, progress=None) -> tuple[list[tuple[int, str, str]], Counter]:
    """[(source id, old text, new text)] and counts of what was skipped and why."""
    progress = progress or (lambda *a: None)
    old_font = _original_font(project)
    old_box = boxfit.BoxIndex(project.db(), old_font)
    gf = gamefont.for_project(project)
    rows = project.db().execute("SELECT id, text, translation FROM source"
                                " WHERE translation IS NOT NULL AND translation != ''").fetchall()
    changes, stats = [], Counter()
    for i, (sid, src, tr) in enumerate(rows):
        if i % 2000 == 0:
            progress("Re-wrapping", i, len(rows))
        box = old_box.get(sid, src)
        if not ("\n" in src or box["lines"] > 1):
            stats["single-line"] += 1
            continue
        if wrap(tr, src, old_font, width=box["width"]) != tr:
            stats["breaks set by hand (kept)"] += 1
            continue
        new = gf.wrap(sid, src, tr)
        if new == tr:
            stats["unchanged"] += 1
        else:
            stats["re-wrapped"] += 1
            stats["lines before"] += tr.count("\n") + 1
            stats["lines after"] += new.count("\n") + 1
            changes.append((sid, tr, new))
    progress("Re-wrapping", len(rows), len(rows))
    return changes, stats


def plan_overflow(project, progress=None) -> tuple[list[tuple[int, str, str]], Counter]:
    """[(source id, old text, new text)] for texts that overflow now and fit re-wrapped."""
    progress = progress or (lambda *a: None)
    gf = gamefont.for_project(project)
    rows = project.db().execute("SELECT id, text, translation FROM source"
                                " WHERE translation IS NOT NULL AND translation != ''").fetchall()
    # battle messages are wrapped by the game for each window (export.battle_reflow); the fit
    # check assumes the longest name and re-broke them mid-sentence (2026-10-17)
    battle = {r[0] for r in project.db().execute(
        "SELECT source_id FROM entry WHERE entry = 'battle_Text.bxon'")}
    changes, stats = [], Counter()
    for i, (sid, src, tr) in enumerate(rows):
        if i % 2000 == 0:
            progress("Re-wrapping", i, len(rows))
        if sid in battle:
            continue
        try:
            if gf.fits(sid, src, tr):
                continue
            new = gf.wrap(sid, src, tr)
            ok = new != tr and gf.fits(sid, src, new)
        except Exception:
            stats["error"] += 1
            continue
        if not ok:
            stats["overflows, re-wrap does not help"] += 1
            continue
        stats["re-wrapped"] += 1
        changes.append((sid, tr, new))
    progress("Re-wrapping", len(rows), len(rows))
    return changes, stats


def apply(project, changes) -> str:
    """Write the new line breaks (after a backup of the database). Returns the backup path."""
    backup = project.db_path + time.strftime(".before-rewrap-%Y%m%d-%H%M%S")
    src = sqlite3.connect(project.db_path)
    dst = sqlite3.connect(backup)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    db = project.db()
    now = time.time()
    # only rows that still hold the text the plan was made from
    db.executemany("UPDATE source SET translation=?, updated=? WHERE id=? AND translation=?",
                   [(new, now, sid, old) for sid, old, new in changes])
    db.commit()
    return backup


def main():
    import argparse
    from .project import Project
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                      "workspace"))
    ap.add_argument("--apply", action="store_true", help="write the changes (default: only report)")
    ap.add_argument("--overflow", action="store_true", help="re-wrap texts that overflow their windows now")
    args = ap.parse_args()
    project = Project(args.workdir)
    changes, stats = (plan_overflow if args.overflow else plan)(project)
    print(dict(stats))
    if args.apply and changes:
        print("backup:", apply(project, changes))


if __name__ == "__main__":
    main()
