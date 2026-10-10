"""Reference translation from the PC (Steam) version.

The PC release ships the Japanese script next to the official English one. Each 3DS
string is matched against the PC Japanese text:
  exact  - same text after normalisation (ruby, line breaks, pauses, spacing, width removed):
           the official English is taken over automatically (origin 'pc')
  fuzzy  - the most similar PC lines are kept as suggestions; the editor shows them with a
           diff, and the AI translator gets them as context ("official reference")
Strings without anything similar fall back to plain AI translation.
"""
from __future__ import annotations

import difflib
import re
import time
import unicodedata
from collections import Counter, defaultdict

from .project import Project

SCHEMA = """
CREATE TABLE IF NOT EXISTS pc_text (
    id INTEGER PRIMARY KEY, file TEXT, key TEXT, jp TEXT, en TEXT, jp_norm TEXT);
CREATE INDEX IF NOT EXISTS pc_text_norm ON pc_text(jp_norm);
CREATE TABLE IF NOT EXISTS pc_match (
    source_id INTEGER, rank INTEGER, pc_id INTEGER, score REAL, kind TEXT, en TEXT,
    PRIMARY KEY (source_id, rank));
"""

DEFAULT_PC_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\DRAGON QUEST XI"

EXACT = 1.0
SUGGEST_MIN = 0.55      # below this a PC line is not shown as "similar"
CONTEXT_MIN = 0.55      # ... nor given to the AI as reference
TOP_N = 3

_TAG = re.compile(r"\{ruby:([^|{}]*)\|[^{}]*\}|\{v:[0-9A-Fa-f]+\}|\{[^{}]*\}")
# PC markup: tags that stand for a runtime value become the same marker as a 3DS {v:..}
_PC_VALUE = re.compile(r"<(?:pc|LM_[^<>]*|Target|Caster|ActionName|MenuItemName|MenuCharName|M_ItemName|"
                       r"Damage|Status\d*|TriggerChara|PartnerChara\d*|ItemName|MonsterName|Value\d*|Num\d*)>|%[ds]",
                       re.I)
_PC_OTHER = re.compile(r"<[^<>]*>")


def _strip_pc(text: str) -> str:
    return _PC_OTHER.sub("", _PC_VALUE.sub("{v:0}", text))


def ensure_schema(project: Project):
    db = project.db()
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(source)")}
    if "origin" not in cols:
        db.execute("ALTER TABLE source ADD COLUMN origin TEXT")
    db.commit()


def normalize_jp(text: str) -> str:
    """Comparable form of Japanese game text from either version."""
    def tag(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)            # ruby: keep the base text
        if m.group(0).startswith("{v:"):
            return "◆"                   # runtime variable (name, number...)
        return ""                        # pauses and other control codes
    t = _TAG.sub(tag, _strip_pc(text))
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"[\s　]+", "", t)
    t = t.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    return t


def _grams(s: str, n: int = 2) -> set[str]:
    if len(s) <= n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


class PcIndex:
    """Exact and fuzzy lookup of normalised PC Japanese lines."""

    def __init__(self, rows: list[tuple[int, str]]):
        self.norm = {}
        self.exact: dict[str, list[int]] = defaultdict(list)
        self.grams: dict[str, list[int]] = defaultdict(list)
        for pid, norm in rows:
            if not norm:
                continue
            self.norm[pid] = norm
            self.exact[norm].append(pid)
        for norm, pids in self.exact.items():
            for g in _grams(norm):
                self.grams[g].append(pids[0])          # one representative per distinct text
        n = max(len(self.exact), 1)
        self.common = {g for g, l in self.grams.items() if len(l) > max(2000, n // 20)}

    def lookup(self, norm: str, top: int = TOP_N) -> list[tuple[int, float, str]]:
        """[(pc_id, score, kind)] best first."""
        if not norm:
            return []
        if norm in self.exact:
            return [(self.exact[norm][0], EXACT, "exact")]
        gs = _grams(norm)
        hits = Counter()
        for g in gs:
            if g in self.common:
                continue
            for pid in self.grams.get(g, ()):
                hits[pid] += 1
        if not hits:
            return []
        # cheap prefilter by shared n-grams (Dice), exact ratio on the best candidates only
        cand = []
        ln = len(gs)
        for pid, c in hits.most_common(60):
            dice = 2 * c / (ln + len(_grams(self.norm[pid])))
            cand.append((dice, pid))
        cand.sort(reverse=True)
        out = []
        for _, pid in cand[:15]:
            out.append((pid, similarity(norm, self.norm[pid]), "fuzzy"))
        out.sort(key=lambda x: -x[1])
        return [x for x in out[:top] if x[1] >= SUGGEST_MIN]


def store_pc_text(project: Project, records: list[dict]):
    """records: [{file, key, jp, en}] -> pc_text (replaces earlier imports)."""
    ensure_schema(project)
    db = project.db()
    db.execute("DELETE FROM pc_text")
    db.execute("DELETE FROM pc_match")
    db.executemany("INSERT INTO pc_text (file, key, jp, en, jp_norm) VALUES (?,?,?,?,?)",
                   [(r["file"], r["key"], r["jp"], r["en"], normalize_jp(r["jp"])) for r in records
                    if r.get("en")])
    db.commit()


def match_all(project: Project, convert, progress=None, cancel=None) -> dict:
    """Match every 3DS source string. convert(pc_en, source_jp) -> English in 3DS markup.

    Exact matches replace untranslated strings and machine drafts (never hand edits)."""
    from .mt import _postprocess
    from .export import validate
    from .pcmatch import has_markup
    progress = progress or (lambda *a: None)
    ensure_schema(project)
    db = project.db()
    idx = PcIndex(db.execute("SELECT id, jp_norm FROM pc_text").fetchall())
    pc = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, jp, en FROM pc_text")}
    sources = db.execute("SELECT id, text, status, origin FROM source").fetchall()
    db.execute("DELETE FROM pc_match")
    stats = {"strings": len(sources), "exact": 0, "applied": 0, "fuzzy": 0, "none": 0, "markup": 0}
    rows, applied = [], []
    t0 = time.time()
    for i, s in enumerate(sources):
        if cancel is not None and cancel.is_set():
            break
        if i % 500 == 0:
            progress("Matching against the PC script", i, len(sources))
        res = idx.lookup(normalize_jp(s["text"]))
        if not res:
            stats["none"] += 1
            continue
        for rank, (pid, score, kind) in enumerate(res):
            # PC markup -> 3DS tags, then the same clean-up and wrapping (window width) as AI text
            en = _postprocess(project, s["text"], convert(pc[pid][1], s["text"]), s["id"])
            rows.append((s["id"], rank, pid, score, kind, en))
        if res[0][2] == "exact":
            stats["exact"] += 1
            # untranslated, machine drafts and earlier official imports are replaced; hand edits stay
            if s["status"] <= 1 or s["origin"] == "pc":
                en = rows[-len(res)][5]
                # unresolvable PC markup (<pc> where the 3DS line has no name): keep it as a
                # draft for the AI to rephrase instead of a finished line
                # ... and so is an official line that drops a {v:..} placeholder of the 3DS text
                status = 1 if (has_markup(en) or validate(s["text"], en)) else 2
                stats["markup"] += status == 1
                applied.append((en, status, time.time(), s["id"]))
        else:
            stats["fuzzy"] += 1
    db.executemany("INSERT INTO pc_match VALUES (?,?,?,?,?,?)", rows)
    db.executemany("UPDATE source SET translation=?, status=?, origin='pc', updated=? WHERE id=?"
                   " AND (status<=1 OR origin='pc')", applied)
    db.commit()
    stats["applied"] = len(applied)
    stats["seconds"] = round(time.time() - t0, 1)
    progress("Matching against the PC script", len(sources), len(sources))
    return stats


def suggestions(project: Project, source_id: int) -> list[dict]:
    db = project.db()
    try:
        rows = db.execute("SELECT m.rank, m.score, m.kind, m.en, p.jp, p.en AS pc_en, p.key, p.file"
                          " FROM pc_match m JOIN pc_text p ON p.id = m.pc_id WHERE m.source_id=? ORDER BY m.rank",
                          (source_id,)).fetchall()
    except Exception:          # no PC import yet
        return []
    src = db.execute("SELECT text FROM source WHERE id=?", (source_id,)).fetchone()
    out = []
    for r in rows:
        d = dict(r)
        d["diff"] = jp_diff(src["text"] if src else "", r["jp"]) if r["kind"] != "exact" else []
        out.append(d)
    return out


def _display(text: str) -> str:
    """Readable Japanese for the diff: ruby base text, variables as ◆, no control codes."""
    def tag(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)
        return "◆" if m.group(0).startswith("{v:") else ""
    return re.sub(r"\s+", " ", _TAG.sub(tag, _strip_pc(text))).strip()


def jp_diff(a: str, b: str) -> list[list[str]]:
    """[[op, text]] with op equal | del (only in the 3DS text) | ins (only in the PC text)."""
    a, b = _display(a), _display(b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["equal", a[i1:i2]])
        else:
            if i2 > i1:
                out.append(["del", a[i1:i2]])
            if j2 > j1:
                out.append(["ins", b[j1:j2]])
    return out


def context_for(project: Project, source_ids: list[int]) -> dict[int, list[dict]]:
    """Official PC lines similar to each string, for the AI prompt."""
    if not source_ids:
        return {}
    db = project.db()
    try:
        q = ("SELECT m.source_id, m.score, p.jp, m.en FROM pc_match m JOIN pc_text p ON p.id = m.pc_id"
             " WHERE m.score >= ? AND m.source_id IN (%s) ORDER BY m.source_id, m.rank" % ",".join("?" * len(source_ids)))
        rows = db.execute(q, [CONTEXT_MIN] + list(source_ids)).fetchall()
    except Exception:
        return {}
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["source_id"]].append({"japanese": r["jp"], "official_english": r["en"], "similarity": round(r["score"], 2)})
    return out


# ---------------------------------------------------------------------- PC files
# The Western PC release ships English only (plus de/fr/it/es under L10N); the Japanese
# script is not included. Text lives in DataTables of the row struct JackDataTableText:
# per row an FName id, a byte length, an FString and an empty property list.
_BINARY = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PC_TABLE_DIRS = ("JackGame/Content/DataTables/Text/", "JackGame/Content/Scenario/")


def _uasset_names(ua: bytes) -> list[str]:
    import struct
    p = 4
    legacy = struct.unpack_from("<i", ua, p)[0]
    p += 4 + (4 if legacy != -4 else 0) + 8
    ncv = struct.unpack_from("<i", ua, p)[0]
    p += 4 + ncv * 20 + 4
    fl = struct.unpack_from("<i", ua, p)[0]
    p += 4 + (fl if fl >= 0 else -2 * fl)
    _, ncount, noff = struct.unpack_from("<Iii", ua, p)
    r, names = noff, []
    for _ in range(ncount):
        ln = struct.unpack_from("<i", ua, r)[0]
        r += 4
        if ln >= 0:
            names.append(ua[r:r + ln - 1].decode("latin-1"))
            r += ln
        else:
            names.append(ua[r:r - 2 * ln - 2].decode("utf-16-le"))
            r += -2 * ln
        r += 4                                 # name hashes
    return names


def _fstr(b: bytes, p: int) -> tuple[str, int]:
    import struct
    n = struct.unpack_from("<i", b, p)[0]
    p += 4
    if n >= 0:
        return b[p:p + n].rstrip(b"\0").decode("utf-8", "replace"), p + n
    return b[p:p - 2 * n].decode("utf-16-le", "replace").rstrip("\0"), p - 2 * n


def read_text_table(ua: bytes, ue: bytes) -> list[tuple[str, str]]:
    """[(row id, text)] of a JackDataTableText DataTable (uasset + uexp)."""
    import struct
    names = _uasset_names(ua)
    p = 0
    while True:                                # tagged properties of the DataTable object
        idx = struct.unpack_from("<i", ue, p)[0]
        p += 8
        if names[idx] == "None":
            break
        p += 8                                 # property type
        size = struct.unpack_from("<i", ue, p)[0]
        p += 8                                 # size + array index
        p += 1 + size                          # property guid flag + value
    p += 4                                     # object guid flag
    nrows = struct.unpack_from("<i", ue, p)[0]
    p += 4
    out = []
    for _ in range(nrows):
        idx, num, ln = struct.unpack_from("<iii", ue, p)
        p += 12
        rid = names[idx] + (f"_{num - 1}" if num else "")
        text, _ = _fstr(ue, p) if ln >= 4 else ("", 0)
        p += ln
        out.append((rid, text))
    return out


_JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")
L10N_EN = "JackGame/Content/L10N/en/"


def _tables(paks, prefix: str = "JackGame/Content/") -> dict[str, object]:
    """table path (relative to prefix, without extension) -> pak holding it; later paks win."""
    out = {}
    for pak in paks:
        for n in pak.names():
            if not n.endswith(".uasset") or not n.startswith(prefix):
                continue
            rel = n[len(prefix):-7]
            if ("JackGame/Content/" + rel).startswith(PC_TABLE_DIRS):
                out[rel] = (pak, n[:-7])
    return out


def _read_rows(pak, base: str) -> dict[str, str]:
    try:
        rows = read_text_table(pak.read(base + ".uasset"), pak.read(base + ".uexp"))
    except Exception:
        return {}                              # not a text table
    if sum(1 for _, t in rows if _BINARY.search(t)) > len(rows) // 4:
        return {}                              # another row layout (party talk, guide)
    return {rid: t for rid, t in rows if t and not _BINARY.search(t)}


def extract_pc(root: str, progress=None, jp_paks: list[str] | None = None) -> list[dict]:
    """Official English of the PC version, paired by row id with Japanese text from a
    Japanese-text pak (a mod that puts the Japanese script into L10N/en) when given.
    -> [{file, key, jp, en}]; jp is empty where no Japanese line is known."""
    import os
    from .uepak import DQXI_KEY, Pak, open_paks
    progress = progress or (lambda *a: None)
    paks = open_paks(os.path.join(root, "Game", "Content", "Paks"), xor_key=DQXI_KEY)
    if not paks:
        raise FileNotFoundError(f"no .pak files under {root}")
    en_tables = {rel: v for rel, v in _tables(paks).items() if not rel.startswith("L10N/")}
    jp_sources = [Pak(p, xor_key=DQXI_KEY) for p in (jp_paks or []) if p and os.path.isfile(p)]
    jp_tables = _tables(jp_sources, L10N_EN) if jp_sources else {}
    recs = []
    for i, (rel, (pak, base)) in enumerate(sorted(en_tables.items())):
        progress("Reading the PC script", i, len(en_tables))
        en = _read_rows(pak, base)
        jp = _read_rows(*jp_tables[rel]) if rel in jp_tables else {}
        short = rel.rsplit("/", 1)[-1]
        for rid, text in en.items():
            if _JP_CHARS.search(text):         # untranslated dev rows of the English build
                continue
            j = jp.get(rid, "")
            recs.append({"file": short, "key": rid, "jp": j if _JP_CHARS.search(j) else "", "en": text})
    for pak in paks + jp_sources:
        pak.close()
    progress("Reading the PC script", len(en_tables), len(en_tables))
    return recs


def convert_en(pc_en: str, source_jp: str) -> str:
    """Official English (PC markup) -> 3DS editor markup."""
    from .pcmatch import convert_en as conv
    return conv(pc_en, source_jp)
