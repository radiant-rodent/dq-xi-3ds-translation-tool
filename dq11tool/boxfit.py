"""How much room a translation has on screen.

The Japanese source only tells how wide *this* string is, but the window or field it is
drawn into is sized for the longest entry it ever shows. So the limit of a string is:
  - UI layout panes and the dialogue window: the window's real width (window(): the pane width
    corrected for its font size and scale; the 350px message window)
  - table strings (items, monsters, dialogue ...): the widest line and the most lines of
    any string in the same table column (file, archive entry, member, table type)
A string used in several places must fit the tightest of them. The result is never
smaller than the Japanese text itself.
"""
from __future__ import annotations

import re
import threading

from .metrics import avg_char_width, line_widths
from .textcodec import encode_str

_PANE = re.compile(r"\((\d+)\D+?(\d+)px\)")
MIN_WIDTH = 40
# categories whose text appears in the scrolling message window
MESSAGE_CATEGORIES = {"NPC Dialogue", "Event & Cutscene Dialogue", "Party Chat", "Battle Text",
                      "Story Guide & Summaries", "Quests", "Tutorials"}

_lock = threading.Lock()
_cache: dict[str, "BoxIndex"] = {}


class BoxIndex:
    def __init__(self, db, font, window=None, movable: set[int] = frozenset()):
        self.font = font
        rows = db.execute("SELECT e.source_id, e.kind, e.file, e.entry, e.member, e.loc, e.label, e.context, e.category,"
                          " e.capacity, s.text FROM entry e JOIN source s ON s.id = e.source_id").fetchall()
        # strings stored in the executable have a fixed slot: characters, not pixels, are the limit
        # (unless the exporter can move them to free space)
        self.slot_chars: dict[int, int] = {}
        for r in rows:
            if r["capacity"] and r["loc"] not in movable:
                c = r["capacity"] // 2 - 1
                self.slot_chars[r["source_id"]] = min(c, self.slot_chars.get(r["source_id"], c))
        own: dict[int, tuple[int, int]] = {}
        lines_of: dict[tuple, list[int]] = {}
        group_l: dict[tuple, int] = {}
        for r in rows:
            sid = r["source_id"]
            if sid not in own:
                lw = line_widths(r["text"], font)
                own[sid] = (max(lw), len(lw), lw)
            w, n, lw = own[sid]
            key = (r["file"], r["entry"], r["member"], r["label"])
            # lines with {v:..} placeholders only have an estimated width; they would
            # inflate the box (a stat line with 6 numbers measured 502px on a 400px screen)
            exact, est = lines_of.setdefault(key, ([], []))
            for x, line in zip(lw, r["text"].split("\n")):
                (est if "{v:" in line else exact).append(x)
            group_l[key] = max(group_l.get(key, 0), n)
        group_w = {key: max(exact or est) for key, (exact, est) in lines_of.items()}
        # Menu/help tables mix texts for differently sized windows (a help window of 8 Japanese
        # characters next to wider confirmation windows), so the widest line of the table
        # overstates the window of most multi-line texts: the game then breaks English words in
        # the middle ("Check what h|appened"). For multi-line texts outside the message window
        # the upper quartile of the table's multi-line texts is used instead (never below the
        # string's own size).
        ml: dict[tuple, list[tuple[int, int]]] = {}
        seen = set()
        for r in rows:
            sid = r["source_id"]
            key = (r["file"], r["entry"], r["member"], r["label"])
            w, n, lw = own[sid]
            if n >= 2 and (key, sid) not in seen:
                seen.add((key, sid))
                exact = [x for x, line in zip(lw, r["text"].split("\n")) if "{v:" not in line]
                ml.setdefault(key, []).append((max(exact or lw), n))
        ml_w, ml_l = {}, {}
        for key, vals in ml.items():
            if len(vals) >= 4:
                ws = sorted(v[0] for v in vals)
                ls = sorted(v[1] for v in vals)
                ml_w[key] = ws[(3 * len(ws)) // 4]
                ml_l[key] = ls[(3 * len(ls)) // 4]
        limits: dict[int, list[int]] = {}
        for r in rows:
            sid = r["source_id"]
            w, n, _ = own[sid]
            key = (r["file"], r["entry"], r["member"], r["label"])
            real = window(r) if window else None
            m = _PANE.search(r["context"] or "") if r["kind"] == "bflyt" and real is None else None
            if m:
                real = int(m.group(1))
            bw = real if real is not None else group_w[key]
            # a short one-line message is still shown in the multi-line message window and may
            # wrap there; labels, names and menu entries stay on one line
            bl = group_l[key] if (n >= 2 or r["category"] in MESSAGE_CATEGORIES) else n
            if real is None and n >= 2 and r["category"] not in MESSAGE_CATEGORIES and key in ml_w:
                bw, bl = ml_w[key], ml_l[key]
            cur = limits.get(sid)
            limits[sid] = [bw, bl] if cur is None else [min(cur[0], bw), min(cur[1], bl)]
        self.limits = {sid: (max(bw, own[sid][0], MIN_WIDTH), max(bl, own[sid][1]))
                       for sid, (bw, bl) in limits.items()}

    def get(self, sid: int, source: str) -> dict:
        lim = self.limits.get(sid)
        if lim is None:   # unknown id: fall back to the source itself
            lw = line_widths(source, self.font)
            lim = (max(max(lw), MIN_WIDTH), len(lw))
        width, lines = lim
        per_line = max(3, int(width / avg_char_width(self.font)))
        budget = per_line if lines < 2 else int(per_line * lines * 0.85)   # wrapping never fills every line
        slot = self.slot_chars.get(sid)
        if slot is not None:
            budget = min(budget, slot)
        return {"width": width, "lines": lines, "chars_per_line": per_line, "max_chars": budget, "slot_chars": slot}

    def fits(self, sid: int, source: str, translation: str) -> bool:
        lim = self.get(sid, source)
        tw = line_widths(translation, self.font)
        if lim["slot_chars"] is not None and len(encode_str(translation)) > lim["slot_chars"]:
            return False
        return max(tw) <= lim["width"] and len(tw) <= lim["lines"]

    def overshoot(self, sid: int, source: str, translation: str) -> float:
        """0 when it fits; otherwise relative excess width plus extra lines."""
        lim = self.get(sid, source)
        tw = line_widths(translation, self.font)
        return max(0, max(tw) - lim["width"]) / lim["width"] + max(0, len(tw) - lim["lines"])


def index(project, font, window=None) -> BoxIndex:
    """Built once per workspace (source texts never change after extraction)."""
    key = (project.db_path, id(font))     # fonts are cached for the process lifetime
    with _lock:
        if key not in _cache:
            _cache[key] = BoxIndex(project.db(), font, window, project.code_movable())
        return _cache[key]


def reset(project=None):
    with _lock:
        if project is None:
            _cache.clear()
        else:
            for k in [k for k in _cache if k[0] == project.db_path]:
                del _cache[k]
