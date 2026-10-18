"""Articles for the name variables of battle lines that have no PC grammar to copy.

In battle_Text.bxon {v:2} / {v:3} are names (attacker / target: a party member or a monster).
An article tag only takes effect when the name inserted at runtime is a common noun (starts
with the {noun} flag, see grammar.py), so {The}{v:2} reads "The slime attacks!" and
"Hero attacks!" from the same line. Tagging every name variable is therefore safe:
  - at the start of a sentence (text start, after . ! ? or a line break): {The}
  - inside a sentence: {the}
  - "The {v:2}s" / "the {v:2}s" (plural made by hand): {The}{v:2}{pl} / {the}{v:2}{pl}
  - after a literal article ("a ", "the ") or another tag: left alone.
A line is changed only when it still fits (gamefont.fits, measured with the widest article)
and its tags still match the source.

    python -m dq11tool.grammar_tag [--apply]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

from . import gamefont
from .export import validate
from .project import Project

FILES = ("battle_Text.bxon",)
NAME_VAR = re.compile(r"\{v:[23]\}")


def tag_names(tr: str) -> str:
    """tr with article tags before its untagged name variables."""
    tr = re.sub(r"\bThe (\{v:[23]\})s\b", r"{The}\1{pl}", tr)
    tr = re.sub(r"\bthe (\{v:[23]\})s\b", r"{the}\1{pl}", tr)
    out, pos = [], 0
    for m in NAME_VAR.finditer(tr):
        before = tr[:m.start()]
        out.append(tr[pos:m.start()])
        pos = m.start()
        if re.search(r"(\{(the|The|a|A)\}|\b(the|The|a|A|an|An) |\bgroup of )$", before):
            continue                    # already has an article (tag or word); "a group of slimes"
        if re.search(r"\}$", before) and not re.search(r"\{CR\}$", before):
            continue                    # right after another variable / tag: leave it
        prev = re.sub(r"(\{CR\}|\s)+$", "", before)
        start = prev == "" or prev[-1] in ".!?\n" or prev.endswith("{CR}")
        out.append("{The}" if start else "{the}")
    out.append(tr[pos:])
    return "".join(out)


def plan(project) -> tuple[list[tuple[int, str, str]], Counter]:
    gf = gamefont.for_project(project)
    q = ",".join("?" * len(FILES))
    rows = project.db().execute(
        f"SELECT DISTINCT s.id, s.text, s.translation FROM source s JOIN entry e ON e.source_id = s.id"
        f" WHERE e.kind = 'bxon' AND e.entry IN ({q}) AND s.translation IS NOT NULL AND s.translation != ''",
        FILES).fetchall()
    changes, stats = [], Counter()
    for sid, src, tr in rows:
        if not NAME_VAR.search(tr):
            continue
        new = tag_names(tr)
        if new == tr:
            stats["nothing to tag"] += 1
            continue
        wrapped = gf.wrap(sid, src, new)
        if validate(src, wrapped):
            stats["tags differ (kept)"] += 1
        elif not gf.fits(sid, src, wrapped):
            stats["too long with articles (kept)"] += 1
        else:
            stats["tagged"] += 1
            changes.append((sid, tr, wrapped))
    return changes, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="workspace")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=30)
    a = ap.parse_args()
    project = Project(a.workdir)
    changes, stats = plan(project)
    for sid, old, new in changes[:a.show]:
        print(f"{sid}: {old!r}\n   -> {new!r}")
    print(dict(stats))
    if a.apply and changes:
        from .pcnames import apply_simple
        print("backup:", apply_simple(project, changes, "nametags"))


if __name__ == "__main__":
    main()
