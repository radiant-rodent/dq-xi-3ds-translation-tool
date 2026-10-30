"""Put the exact PC names back into name tables (gamefont.NAME_FIELDS, e.g. monster names).

Earlier the box of a name was the widest Japanese line of its table (~72 px), so long names
were abbreviated ("Metl slime"). Names are now measured against the panes they are really
shown in (two lines of the battle target label; one-line lists are fitted by the exporter,
export.RUNTIME_FIT). A name is replaced only when an exact PC match exists, it fits after
wrapping. Hand edits are replaced too: they were made to avoid crashes and overflowing panes,
the official text is the target.

    python -m dq11tool.pcnames [--workdir workspace] [--apply]
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter

from . import gamefont
from .export import validate
from .project import Project


def plan(project) -> tuple[list[tuple[int, str, str]], Counter]:
    gf = gamefont.for_project(project)
    q = ",".join("?" * len(gamefont.NAME_FIELDS))
    rows = project.db().execute(
        f"SELECT DISTINCT s.id, s.text, s.translation, s.origin, m.en FROM source s"
        f" JOIN entry e ON e.source_id = s.id"
        f" JOIN pc_match m ON m.source_id = s.id AND m.rank = 0 AND m.kind = 'exact'"
        f" WHERE e.kind = 'bxon' AND e.entry IN ({q})", tuple(gamefont.NAME_FIELDS)).fetchall()
    changes, stats = [], Counter()
    for sid, src, tr, origin, en in rows:
        if gf.name_field(sid) is None:
            stats["also used elsewhere"] += 1
            continue
        new = gf.wrap(sid, src, en)
        if new == tr:
            stats["already official"] += 1
        elif validate(src, new):
            stats["tags differ (kept)"] += 1
        elif not gf.fits(sid, src, new):
            stats["too long even now (kept)"] += 1
        else:
            stats["restored"] += 1
            changes.append((sid, tr, new))
    return changes, stats


def plan_labels(project) -> tuple[list[tuple[int, str, str]], Counter]:
    """Layout labels (gamefont.label_only): the exact PC term when it fits the pane with the
    exporter's glyph narrowing (gamefont.LABEL_SQUEEZE). Reviewed strings are kept: short
    labels such as やめる have false exact matches ("I don't want\\nanything.", issue #5)."""
    gf = gamefont.for_project(project)
    rows = project.db().execute(
        "SELECT s.id, s.text, s.translation, m.en FROM source s"
        " JOIN pc_match m ON m.source_id = s.id AND m.rank = 0 AND m.kind = 'exact'"
        " WHERE s.translation IS NOT NULL AND s.translation != '' AND s.status < 3").fetchall()
    changes, stats = [], Counter()
    for sid, src, tr, en in rows:
        if not gf.label_only(sid):
            continue
        new = en if src.count("\n") == en.count("\n") else gf.wrap(sid, src, en)
        if new == tr:
            stats["already official"] += 1
        elif validate(src, new):
            stats["tags differ (kept)"] += 1
        elif not gf.fits(sid, src, new):
            stats["too long even narrowed (kept)"] += 1
        else:
            stats["restored"] += 1
            changes.append((sid, tr, new))
    return changes, stats


def official_dialogue(source: str, en: str) -> str:
    """The PC line in 3DS form: <pc> (the hero's name in the PC script) becomes {v:1} when
    the Japanese line has that variable."""
    if "<pc>" in en and "{v:1}" in source:
        en = en.replace("<pc>", "{v:1}")
    return en


def dialogue_problems(source: str, translation: str) -> list[str]:
    """validate() for official lines (they may leave out the hero's name {v:1})."""
    return validate(source, translation, official=True)


def plan_dialogue(project) -> tuple[list[tuple[int, str, str]], list[int], set[int], Counter]:
    """Message-window strings (gamefont.paged): the exact PC line when it fits the window
    (3 lines of gamefont.MESSAGE_WINDOW px, at most MESSAGE_MAX_UNITS units). Also returns the
    ids whose current translation no longer fits (to be shortened)."""
    from .textcodec import encode_str
    gf = gamefont.for_project(project)
    rows = project.db().execute(
        "SELECT s.id, s.text, s.translation, m.en FROM source s LEFT JOIN pc_match m"
        " ON m.source_id = s.id AND m.rank = 0 AND m.kind = 'exact'"
        " WHERE s.translation IS NOT NULL AND s.translation != ''").fetchall()
    changes, too_long, official, stats = [], [], set(), Counter()
    for sid, src, tr, en in rows:
        if not gf.paged(sid):
            continue
        if en:
            new = gf.wrap(sid, src, official_dialogue(src, en))
            if new == tr:
                stats["already official"] += 1
                continue
            if (not dialogue_problems(src, new) and gf.fits(sid, src, new)
                    and len(encode_str(new)) <= gamefont.MESSAGE_MAX_UNITS):
                stats["official line restored"] += 1
                changes.append((sid, tr, new))
                official.add(sid)
                continue
            stats["official line too long (kept)"] += 1
        cur = gf.wrap(sid, src, tr)
        if not gf.fits(sid, src, cur):
            stats["current too long (draft)"] += 1
            too_long.append(sid)
        elif cur != tr:
            stats["re-wrapped"] += 1
            changes.append((sid, tr, cur))
    return changes, too_long, official, stats


def _bare(text: str) -> str:
    """Comparable form: no tags, no line breaks, single spaces, no case."""
    import re
    t = re.sub(r"\{[^{}]*\}", " ", text or "")
    return re.sub(r"\s+", " ", t).strip().lower()


def _keep_pauses(old: str, new: str) -> str:
    """{CR} pauses of `old` put into `new` after the same word (words counted without tags)."""
    import re
    if "{CR}" not in old or "{CR}" in new:
        return new
    words = lambda s: len(re.sub(r"\{[^{}]*\}", "", s).split())
    at = [words(old[:m.start()]) for m in re.finditer(r"\{CR\}", old)]
    out, n = [], 0
    for tok in re.split(r"(\s+)", new):
        out.append(tok)
        if tok.strip() and re.sub(r"\{[^{}]*\}", "", tok).strip():
            n += 1
            if n in at:
                out.append("{CR}")
    return "".join(out)


def plan_grammar(project) -> tuple[list[tuple[int, str, str]], Counter]:
    """Official lines whose PC original has grammar macros (articles, singular/plural): the
    current flattened text ("{v:2} takes {v:8} points") becomes the tagged form ("{The}{v:2}
    takes {v:8} {n:point|points}"), resolved in game by grammar.py's code patch. Only lines
    whose translation still is the official text are changed."""
    import re
    from .pcmatch import convert_en
    from .textcodec import encode_str, tags_of
    gf = gamefont.for_project(project)
    rows = project.db().execute(
        "SELECT s.id, s.text, s.translation, p.en FROM source s"
        " JOIN pc_match m ON m.source_id = s.id AND m.rank = 0 AND m.kind = 'exact'"
        " JOIN pc_text p ON p.id = m.pc_id"
        " WHERE s.translation IS NOT NULL AND s.translation != ''"
        " AND (p.en LIKE '%<LM_Def%' OR p.en LIKE '%<LM_Indef%' OR p.en LIKE '%<LM_Plr%'"
        " OR p.en LIKE '%<LM_IfSing%')").fetchall()
    changes, stats = [], Counter()
    for sid, src, tr, pc in rows:
        flat = official_dialogue(src, convert_en(pc, src))
        tagged = official_dialogue(src, convert_en(pc, src, grammar=True))
        if "{the}" in tr or "{The}" in tr or "{a}" in tr or "{A}" in tr or "{n:" in tr or "{pl}" in tr:
            stats["already tagged"] += 1
            continue
        if _bare(tr) != _bare(flat):
            stats["not the official text (kept)"] += 1
            continue
        if tagged == flat:
            stats["no grammar for the 3DS variables"] += 1
            continue
        tagged = _keep_pauses(tr, tagged)
        for t in tags_of(tr):                 # e.g. a trailing {v:5} the PC line has no tag for
            if t not in tags_of(tagged) and tr.rstrip().endswith(t):
                tagged += t
        new = gf.wrap(sid, src, tagged)
        if re.search(r"<[A-Za-z_][^<>]*>", new):
            stats["PC markup left (kept)"] += 1
        elif dialogue_problems(src, new):
            stats["tags differ (kept)"] += 1
        elif not gf.fits(sid, src, new) or len(encode_str(new)) > gamefont.MESSAGE_MAX_UNITS:
            stats["too long with articles (kept)"] += 1
        else:
            stats["tagged"] += 1
            changes.append((sid, tr, new))
    return changes, stats


def _nj(s: str) -> str:
    import re
    s = re.sub(r"\{ruby:([^|{}]*)\|[^{}]*\}", r"\1", s or "")
    return re.sub(r"[\s　]", "", s)


def plan_nouns(project) -> tuple[list[tuple[int, str, str]], Counter]:
    """{noun} flag on common-noun names of the name tables (gamefont.NAME_FIELDS): the PC
    battle name (+Monster+DisplayName) starts in lower case ("slime"; proper names such as
    "Jasper" do not). Names without a PC counterpart (3DS-only monsters) are taken as common
    nouns, and their later words go to lower case like the PC names ("Flame Hound" ->
    "Flame hound"). grammar.py writes them as "the slime" / "a flame hound" in sentences."""
    import re
    db = project.db()
    q = ",".join("?" * len(gamefont.NAME_FIELDS))
    rows = db.execute(
        f"SELECT DISTINCT s.id, s.text, s.translation FROM source s JOIN entry e ON e.source_id = s.id"
        f" WHERE e.kind = 'bxon' AND e.entry IN ({q}) AND s.translation IS NOT NULL AND s.translation != ''",
        tuple(gamefont.NAME_FIELDS)).fetchall()
    by_jp, by_en = {}, {}
    for jp, en in db.execute("SELECT jp, en FROM pc_text WHERE key LIKE '+Monster+DisplayName%'"):
        by_jp.setdefault(_nj(jp), set()).add(en)
        by_en.setdefault(en.lower(), set()).add(en)
    changes, stats = [], Counter()
    for sid, src, tr in rows:
        if tr.startswith("{noun}"):
            stats["already flagged"] += 1
            continue
        ens = by_jp.get(_nj(src)) or by_en.get(re.sub(r"\s+", " ", tr).lower())
        if ens and not all(e[:1].islower() for e in ens):
            stats["proper name (no article)"] += 1
            continue
        new = tr
        if not ens:                            # sentence case like the PC names
            new = re.sub(r"(?<=[\s-])([A-Z])(?=[a-z])", lambda m: m.group(1).lower(), tr)
            stats["3DS-only, sentence case"] += new != tr
        stats["flagged"] += 1
        changes.append((sid, tr, "{noun}" + new))
    return changes, stats


def apply_simple(project, changes, tag: str) -> str:
    backup = _backup(project, tag)
    db = project.db()
    now = time.time()
    db.executemany("UPDATE source SET translation=?, updated=? WHERE id=? AND translation=?",
                   [(new, now, sid, old) for sid, old, new in changes])
    db.commit()
    return backup


def apply_dialogue(project, changes, too_long, official) -> str:
    backup = _backup(project, "pcdialogue")
    db = project.db()
    now = time.time()
    db.executemany("UPDATE source SET translation=?, updated=? WHERE id=? AND translation=?",
                   [(new, now, sid, old) for sid, old, new in changes])
    db.executemany("UPDATE source SET status=1, note=TRIM(COALESCE(note, '') || ' [too long for the"
                   " 3-line message window: shorten]'), updated=? WHERE id=? AND COALESCE(note, '')"
                   " NOT LIKE '%too long for the 3-line%'", [(now, sid) for sid in too_long])
    db.executemany("UPDATE source SET origin='pc', status=2 WHERE id=? AND translation=?",
                   [(sid, new) for sid, _, new in changes if sid in official])
    db.commit()
    return backup


def _backup(project, tag: str) -> str:
    backup = project.db_path + time.strftime(f".before-{tag}-%Y%m%d-%H%M%S")
    src = sqlite3.connect(project.db_path)
    dst = sqlite3.connect(backup)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return backup


def apply(project, changes) -> str:
    backup = _backup(project, "pcnames")
    db = project.db()
    now = time.time()
    db.executemany("UPDATE source SET translation=?, origin='pc', status=2, updated=? WHERE id=? AND"
                   " (translation=? OR translation IS NULL)",
                   [(new, now, sid, old) for sid, old, new in changes])
    db.commit()
    return backup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="workspace")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dialogue", action="store_true", help="message-window strings instead of name tables")
    ap.add_argument("--labels", action="store_true", help="layout labels instead of name tables")
    a = ap.parse_args()
    project = Project(a.workdir)
    if a.dialogue:
        changes, too_long, official, stats = plan_dialogue(project)
    elif a.labels:
        changes, stats = plan_labels(project)
    else:
        changes, stats = plan(project)
    for k, v in stats.most_common():
        print(f"{v:6}  {k}")
    if a.apply and a.dialogue and (changes or too_long):
        print("backup:", apply_dialogue(project, changes, too_long, official))
    elif a.apply and changes:
        print("backup:", apply(project, changes))


if __name__ == "__main__":
    main()
