"""Match 3DS strings to official PC English lines through English.

The Western PC release has no Japanese script, so the comparison runs on English: a
string's machine draft (or finished translation) is looked up among the official lines,
and the AI then decides which candidate, if any, is the official translation of that
exact line (see mt.pick_official).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

from .pcref import TOP_N, ensure_schema, similarity
from .project import Project

_TAG3DS = re.compile(r"\{[^{}]*\}")
_PC_TAG = re.compile(r"<[^<>]*>")
# PC tags that stand for a runtime value (name, number ...)
_PLACEHOLDER = re.compile(r"<(?:pc|LM_[^<>]*|MenuCharName|Target|ActionName|Damage|Status\d*|Caster|ItemName|"
                          r"MonsterName|Value\d*|Num\d*|Gold|Exp)>|%[ds]", re.I)
# PC tags without visible effect on 3DS (voice, facial animation, camera, flow control)
_DROP = re.compile(r"<(?:end|nametab_[^<>]*|voice=[^<>]*|facial_[^<>]*|motion=[^<>]*|se=[^<>]*|me=[^<>]*|"
                   r"textid=[^<>]*|no_lipsync|close|focus|restore_turn|btlmsg_time=[^<>]*|fade_in_page=[^<>]*|"
                   r"icon_exc|center|shoptextid=[^<>]*|yesno|blank_line|wait=[^<>]*|camera[^<>]*|LM_Cap)>", re.I)
_V = re.compile(r"\{v:[0-9A-Fa-f]+\}")

MIN_SCORE = 0.5


def normalize_en(text: str) -> str:
    """Comparable English of either source: no markup, lower case, single spaces."""
    t = _TAG3DS.sub(" ", text)
    t = _PC_TAG.sub(" ", t)
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def pc_display(text: str) -> str:
    """PC line for reading: <br> as newline, stage directions removed, placeholders kept."""
    t = _DROP.sub("", text)
    t = re.sub(r"<br>", "\n", t, flags=re.I)
    return t.strip()


# Which 3DS variable a PC placeholder stands for, measured on the exactly matching lines:
# <pc> was {v:1} in 1746/1746 pairs, <Target>/<Caster>/<MenuCharName>/<MonsterName> {v:2},
# item and action names {v:5}.
_ROLE_CODES = {
    "pc": ("{v:1}",), "turn_pc": ("{v:1}",), "partnerchara": ("{v:1}", "{v:2}"),
    "target": ("{v:2}", "{v:3}"), "caster": ("{v:2}", "{v:3}"), "menucharname": ("{v:2}",),
    "monstername": ("{v:2}", "{v:3}"), "triggerchara": ("{v:2}",),
    "menuitemname": ("{v:5}",), "actionname": ("{v:5}",), "m_itemname": ("{v:5}",), "itemname": ("{v:5}",),
}
_ANY_TAG = re.compile(r"<([^<>]*)>")
_ARG = r"((?:[^()<>,]|<[^<>]*>)*)"
_IF_MACRO = re.compile(r"<LM_If\w*\(" + _ARG + "," + _ARG + r"\)>", re.I)
_NAME_CODES = {"{v:1}", "{v:2}", "{v:5}"}     # hero, target/caster, item: never a number
_NUMERIC = {"value", "damage", "num", "gold", "exp", "count", "point", "menuitemcount", "m_itemcount"}


def _role(tag: str) -> str:
    t = re.sub(r"^LM_(?:DefSgl|IndefSgl|DefPl|IndefPl|Cap)_", "", tag, flags=re.I)
    return re.sub(r"\d+$", "", t).lower()


def convert_en(pc_en: str, source_jp: str) -> str:
    """Official English (PC markup) -> 3DS editor markup.

    Name placeholders are mapped by role onto the {v:..} tags the 3DS source has (see
    _ROLE_CODES); grammar macros are resolved (<LM_IfSing_X(point,points)> -> points,
    <LM_Gender_X(his,her,its)> -> their); stage directions are dropped. A placeholder
    whose 3DS counterpart cannot be determined stays visible (<pc>) for the translator
    or the AI. Line breaks are left to the 3DS auto-wrap."""
    src = _V.findall(source_jp)
    unused = list(dict.fromkeys(src))

    def tag(m: re.Match) -> str:
        body = m.group(1)
        low = body.lower()
        if low == "br":
            return " "
        g = re.match(r"LM_If\w*\(([^,()]*),([^()]*)\)", body, re.I)
        if g:                                  # IfSing(point,points), IfSolo(name,the party): plural/party form
            return g.group(2)
        if re.match(r"LM_Gender_", body, re.I):
            return "their"
        role = _role(body)
        if role in _NUMERIC:                   # %d / %s / <Damage>: a number the source also has
            nums = [c for c in unused if c not in _NAME_CODES] if len(unused) > 1 else list(unused)
            if len(nums) == 1:
                code = nums[0]
                unused.remove(code)
                return code
            return m.group(0)
        if role in _ROLE_CODES:
            for code in _ROLE_CODES[role]:
                if code in src:
                    if code in unused:
                        unused.remove(code)
                    return code
            if len(unused) == 1:               # the one variable the source has left
                return unused.pop()
            return m.group(0)
        return ""                              # voice, facial, camera, colour, flow control ...

    cap = "⁣"                              # marks "capitalise the next letter" (<LM_Cap>)
    t = re.sub(r"<LM_Cap>", cap, pc_en, flags=re.I).replace("%d", "<Value>").replace("%s", "<Value>")
    # the 3DS script does not branch on party size / plural: take the party / plural form
    # (second argument) before anything else, even when the arguments contain tags
    t = _IF_MACRO.sub(lambda m: m.group(2), t)
    # tags nest (<LM_DefSgl_<...>>): resolve the innermost ones first, repeatedly
    for _ in range(4):
        new = _ANY_TAG.sub(tag, t)
        if new == t:
            break
        t = new
    t = re.sub(cap + r"(\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t).replace(cap, "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:1].upper() + t[1:] if t[:1].islower() else t


_LEFTOVER = re.compile(r"<[A-Za-z_][^<>]*>")


def has_markup(text: str) -> bool:
    """True when PC markup could not be resolved (e.g. <pc> where the 3DS line has no name)."""
    return bool(_LEFTOVER.search(text or ""))


class EnIndex:
    """Fuzzy lookup of official English lines by word n-grams."""

    def __init__(self, rows: list[tuple[int, str]]):
        self.norm: dict[int, str] = {}
        self.exact: dict[str, int] = {}
        self.grams: dict[str, list[int]] = defaultdict(list)
        for pid, en in rows:
            n = normalize_en(en)
            if len(n) < 2 or n in self.exact:
                continue
            self.norm[pid] = n
            self.exact[n] = pid
            for g in self._grams(n):
                self.grams[g].append(pid)
        total = max(len(self.norm), 1)
        self.common = {g for g, l in self.grams.items() if len(l) > max(3000, total // 30)}

    @staticmethod
    def _grams(n: str) -> set[str]:
        w = n.split()
        if len(w) == 1:                        # single words: character trigrams as well
            return {"w:" + w[0]} | {n[i:i + 3] for i in range(max(1, len(n) - 2))}
        return {"w:" + x for x in w} | {w[i] + " " + w[i + 1] for i in range(len(w) - 1)}

    def lookup(self, query: str, top: int = TOP_N, min_score: float = MIN_SCORE) -> list[tuple[int, float, str]]:
        q = normalize_en(query)
        if not q:
            return []
        if q in self.exact:
            return [(self.exact[q], 1.0, "exact")]
        hits = Counter()
        for g in self._grams(q):
            if g in self.common:
                continue
            for pid in self.grams.get(g, ()):
                hits[pid] += 1
        out = []
        for pid, _ in hits.most_common(40):
            n = self.norm[pid]
            if not 0.4 < len(n) / len(q) < 2.5:
                continue
            out.append((pid, similarity(q, n), "similar"))
        out.sort(key=lambda x: -x[1])
        return [x for x in out[:top] if x[1] >= min_score]


_index: dict[str, EnIndex] = {}


def index(project: Project) -> EnIndex | None:
    ensure_schema(project)
    key = project.db_path
    if key not in _index:
        rows = project.db().execute("SELECT id, en FROM pc_text").fetchall()
        if not rows:
            return None
        _index[key] = EnIndex([(r[0], r[1]) for r in rows])
    return _index[key]


def reset(project: Project):
    _index.pop(project.db_path, None)


def candidates(project: Project, source_text: str, draft: str, top: int = TOP_N) -> list[dict]:
    """Official lines similar to a draft: [{pc_id, score, kind, pc_en, en}] best first."""
    idx = index(project)
    if idx is None or not draft:
        return []
    db = project.db()
    out = []
    for pid, score, kind in idx.lookup(draft, top):
        pc_en = db.execute("SELECT en FROM pc_text WHERE id=?", (pid,)).fetchone()[0]
        out.append({"pc_id": pid, "score": round(score, 3), "kind": kind, "pc_en": pc_en,
                    "en": convert_en(pc_en, source_text)})
    return out


def store_candidates(project: Project, source_id: int, cands: list[dict]):
    db = project.db()
    db.execute("DELETE FROM pc_match WHERE source_id=?", (source_id,))
    db.executemany("INSERT INTO pc_match VALUES (?,?,?,?,?,?)",
                   [(source_id, i, c["pc_id"], c["score"], c["kind"], c["en"]) for i, c in enumerate(cands)])


def match_drafts(project: Project, progress=None, cancel=None) -> dict:
    """Find official candidates for every string that has a translation (draft or done)."""
    progress = progress or (lambda *a: None)
    reset(project)
    if index(project) is None:
        raise RuntimeError("import the PC script first")
    db = project.db()
    rows = db.execute("SELECT id, text, translation FROM source"
                      " WHERE translation IS NOT NULL AND translation != ''").fetchall()
    db.execute("DELETE FROM pc_match")
    stats = {"strings": len(rows), "exact": 0, "similar": 0, "none": 0}
    for i, r in enumerate(rows):
        if cancel is not None and cancel.is_set():
            break
        if i % 500 == 0:
            progress("Finding official PC lines", i, len(rows))
            db.commit()
        c = candidates(project, r["text"], r["translation"])
        if not c:
            stats["none"] += 1
            continue
        stats["exact" if c[0]["kind"] == "exact" else "similar"] += 1
        store_candidates(project, r["id"], c)
    db.commit()
    progress("Finding official PC lines", len(rows), len(rows))
    return stats
