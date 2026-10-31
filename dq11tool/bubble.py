"""Speech bubbles over NPC heads (CharacterIEData "fukidasi" lines).

Besides the talk lines every CharacterIEData table (npc_ie / book_ie / script ie.bxon) has,
per root entry, an array of 12-byte records (root +0x10 count, +0x14 relptr; record: u32,
relptr text at +4, u32) with the short remarks NPCs make over their heads. The bubble code
(code.bin 0x5F8AB8) lays the text out like this:

  - a text with U+000A is split there, two-line bubble;
  - a longer one (more than 8 characters) without it gets a U+000A inserted after character 8,
    whatever the word ("Ahh, goo" / "d old pep powers...");
  - up to 8 characters stay on one line.

The text pane (hud_balloon_3d.bflyt txt1-4) is 130 x 30 px, font size 13 -> 130 px of 3D12,
two lines. So every bubble text of more than 8 characters needs its own break; `layout` picks
it: two lines, never inside a word, preferably after punctuation and not after a word that
needs the next one ("the", "of", "and"...), the lines about equally wide. Text that is too wide
for 2 x 130 px stays 2 lines (the runtime fit narrows its glyphs, autofit.py).

    python -m dq11tool.bubble workspace            # report: what changes
    python -m dq11tool.bubble workspace --apply    # write the breaks into the translations
"""
from __future__ import annotations

import re

from .ietalk import _target, _u32
from .metrics import line_widths

PANE_WIDTH = 130          # px of BUBBLE_FONT (pane 130 px, font size 13 = 3D12's width)
LINES = 2
FONT = "3D12"
ONE_LINE_CHARS = 8        # the game breaks longer texts after this many characters
_TAG = re.compile(r"\{[^{}]*\}")

# words a line should not end with (they need the next one) / that may start the second line
_BINDING = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "by", "with", "from", "into", "my",
    "your", "his", "her", "our", "their", "its", "this", "that", "these", "those", "some", "any",
    "no", "and", "or", "but", "if", "as", "than", "so", "not", "is", "are", "was", "be", "i",
    "you", "he", "she", "we", "they", "it", "me", "us", "them", "will", "can", "would", "could",
    "should", "must", "may", "might", "do", "does", "did", "have", "has", "had", "just", "very",
    "too", "more", "most", "all", "every", "each", "what", "who", "how", "when", "where", "why",
    "which", "while", "about", "over", "under", "up", "out", "off", "i'm", "i'll", "i've", "i'd",
    "you're", "you'll", "we're", "they're", "it's", "that's", "there's", "don't", "can't",
}
_STARTERS = {"and", "but", "or", "so", "because", "though", "while", "when", "if", "then", "yet"}


def locs(data: bytes) -> set[int]:
    """String positions of the bubble lines in a CharacterIEData bxon."""
    out: set[int] = set()
    if data[0x0C:0x20].split(b"\0", 1)[0] != b"CharacterIEData":
        return out
    root = _target(data, 0x24)
    for i in range(_u32(data, 0x20)):
        e = root + 24 * i
        n, arr = _u32(data, e + 0x10), _target(data, e + 0x14)
        if n and arr is not None:
            for k in range(n):
                t = _target(data, arr + 12 * k + 4)
                if t is not None:
                    out.add(t)
    return out


def entry_ids(project) -> set[int]:
    """Entry ids of the bubble lines (cached in the project meta)."""
    import json
    db = project.db()
    r = db.execute("SELECT value FROM meta WHERE key='bubble_entries'").fetchone()
    if r:
        return set(json.loads(r["value"]))
    from .pack import Pack
    from .ctr.formats import open_image
    rows = db.execute("SELECT id, file, entry, loc FROM entry WHERE label = 'CharacterIEData'").fetchall()
    by_file: dict[tuple, list] = {}
    for x in rows:
        by_file.setdefault((x["file"], x["entry"]), []).append(x)
    ids: set[int] = set()
    f, ncsd, ncch = open_image(project.rom_path)
    try:
        romfs = ncch.romfs()
        files = {x.path: x for x in romfs.files}
        packs: dict[str, Pack] = {}
        for (path, entry), rs in by_file.items():
            if path not in files:
                continue
            if path not in packs:
                packs[path] = Pack.parse(romfs.read_file(files[path]))
            data = next((e.data for e in packs[path].entries if e.name == entry), None)
            if data is None:
                continue
            at = locs(data)
            ids.update(x["id"] for x in rs if x["loc"] in at)
    finally:
        f.close()
    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('bubble_entries', ?)", (json.dumps(sorted(ids)),))
    db.commit()
    return ids


def only_sources(project) -> set[int]:
    """Sources whose every occurrence is a bubble line."""
    ids = entry_ids(project)
    ok: dict[int, bool] = {}
    for r in project.db().execute("SELECT id, source_id FROM entry"):
        ok[r["source_id"]] = ok.get(r["source_id"], True) and r["id"] in ids
    return {s for s, v in ok.items() if v}


def visible_chars(text: str) -> int:
    return len(_TAG.sub("", text).replace("\n", ""))


def _flat(text: str) -> str:
    text = re.sub(r"\{PAGE\}|\{CR\}|\u001f", " ", text)
    return re.sub(r" *\r?\n *", " ", text).strip()


def _cuts(text: str) -> list[tuple[int, int, int]]:
    """(end of line 1, start of line 2, extra cost) positions where a line may break: at a space (not
    inside a {tag}), or after a hyphen / dash inside a word."""
    out = []
    depth = 0
    for i, c in enumerate(text):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0:
            if c == " " and 0 < i < len(text) - 1 and text[i - 1] != " ":
                out.append((i, i + 1, 0))
            elif c in "-—" and 0 < i < len(text) - 1 and text[i + 1].isalpha() and text[i - 1].isalpha():
                out.append((i + 1, i + 1, 25))
    return out


def _word(s: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", s.lower())


def _penalty(a: str, b: str, w1: int, w2: int) -> float:
    last, first = a.split(" ")[-1], b.split(" ")[0]
    p = 0.5 * abs(w1 - w2)
    if re.search(r"[,;:.!?…♪)\"]$", last):
        p -= 20 if re.search(r"[.!?…♪]$", last) else 10
    elif _word(last) in _BINDING:
        p += 45
    if _word(first) in _STARTERS:
        p -= 12
    if w2 < 30 or w1 < 30:
        p += 35                      # a lone short word
    if not re.search(r"[\s]", b) and len(_TAG.sub("x", b)) <= 4:
        p += 15
    return p


def layout(text: str, font) -> str:
    """The text as the export writes it: one line up to 8 characters, else two lines."""
    flat = _flat(text)
    if not flat or visible_chars(flat) <= ONE_LINE_CHARS:
        return flat
    best, best_cost = None, None
    for end, start, extra in _cuts(flat):
        a, b = flat[:end].rstrip(), flat[start:].lstrip()
        if not a or not b:
            continue
        w1, w2 = line_widths(a, font)[0], line_widths(b, font)[0]
        over = max(0, w1 - PANE_WIDTH) + max(0, w2 - PANE_WIDTH)
        cost = max(w1, w2) + _penalty(a, b, w1, w2) + over * 20 + extra
        if best_cost is None or cost < best_cost:
            best, best_cost = f"{a}\n{b}", cost
    return best if best else _split_word(flat, font)


def _split_word(word: str, font) -> str:
    """A single long word ("Urrrgh...", "Buongiorno!"): the trailing dots go on the second
    line, else the word breaks with a hyphen in the middle at a vowel / consonant boundary."""
    m = re.search(r"(\.{2,}|…+)$", word)
    if m and m.start() >= 3 and not re.search(r"\W", word[:m.start()]):
        return word[:m.start()] + "\n" + word[m.start():]
    if re.search(r"[\s{]", word):
        return word
    mid = len(word) / 2
    ok = [i for i in range(3, len(word) - 2) if word[i - 1].lower() in "aeiou" and word[i].lower() not in "aeiou"]
    if not ok:
        return word
    i = min(ok, key=lambda k: abs(k - mid))
    return word[:i] + "-\n" + word[i:]


def valid(text: str, font) -> bool:
    """A translation that is already broken the way the game needs it: 2 lines within the pane."""
    lines = text.split("\n")
    if len(lines) == 1:
        return visible_chars(text) <= ONE_LINE_CHARS
    return len(lines) == LINES and all(l.strip() and max(line_widths(l, font)) <= PANE_WIDTH for l in lines)


def export_text(text: str, font) -> str:
    """What the exporter writes for a bubble line: the translation's own break when it is a
    good one, else a new one."""
    return text if valid(text, font) and text == text.strip() else layout(text, font)


SQUEEZE_OK = 0.8          # glyph level the runtime fit may use without looking cramped
SQUEEZE_MIN = 0.6         # narrowest glyph level (fontnarrow.LEVELS)


def problems(text: str, font) -> list[str]:
    out = []
    lines = text.split("\n")
    w = max(max(line_widths(l, font)) for l in lines)
    if len(lines) > LINES:
        out.append(f"{len(lines)} lines (bubbles have {LINES})")
    if w > PANE_WIDTH / SQUEEZE_MIN:
        out.append(f"TOO LONG: {w} px, the game can only squeeze to {int(PANE_WIDTH / SQUEEZE_MIN)} px: shorten it")
    elif w > PANE_WIDTH / SQUEEZE_OK:
        out.append(f"cramped: {w} px is squeezed to ~{PANE_WIDTH / w:.0%}")
    elif w > PANE_WIDTH:
        out.append(f"{w} px: slightly squeezed (~{PANE_WIDTH / w:.0%})")
    if len(lines) == 1 and visible_chars(text) > ONE_LINE_CHARS:
        out.append("one long word: the game breaks it after 8 letters")
    return out


def main():
    import argparse
    from .metrics import FontSet
    from .project import Project
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace")
    ap.add_argument("--apply", action="store_true", help="write the breaks into the translations")
    a = ap.parse_args()
    project = Project(a.workspace)
    font = FontSet.for_project(project).fonts[FONT]
    db = project.db()
    ids = entry_ids(project)
    by_src: dict[int, list] = {}
    for r in db.execute("SELECT e.id, e.source_id, e.override FROM entry e"):
        if r["id"] in ids:
            by_src.setdefault(r["source_id"], []).append(r)
    mixed = {s for s in by_src
             if db.execute("SELECT COUNT(*) FROM entry WHERE source_id=?", (s,)).fetchone()[0] != len(by_src[s])}
    changed = bad = 0
    for sid in sorted(by_src):
        s = db.execute("SELECT translation FROM source WHERE id=?", (sid,)).fetchone()
        tr = s["translation"]
        if not tr:
            continue
        new = export_text(tr, font)
        pr = problems(new, font)
        flag = " [also used elsewhere]" if sid in mixed else ""
        if new != tr:
            changed += 1
            print(f"{sid}: {tr!r}\n     -> {new!r}{flag}")
            if a.apply and sid not in mixed:
                db.execute("UPDATE source SET translation=? WHERE id=?", (new, sid))
        if pr:
            bad += 1
            print(f"{sid}: {new!r}  PROBLEM: {'; '.join(pr)}")
    if a.apply:
        db.commit()
    print(f"{len(by_src)} bubble strings, {changed} changed, {bad} with problems"
          f"{'' if a.apply else ' (dry run; --apply writes)'}")


if __name__ == "__main__":
    main()
