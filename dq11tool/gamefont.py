"""Which font the game draws a string with, and how much room it has there.

Layout texts name their font in the layout file. Table texts (bxon) and executable strings
have no font of their own; message-window categories use the dialogue font, everything else
is treated as menu text. The DQ VII Latin glyphs make the dialogue font the narrowest, so
an unsure string is measured with the wider menu font: its English comes out shorter than
necessary, never too long.

Message-window text is also fitted into the 2D-mode message window (MESSAGE_2D): 2D14 is
~16-21% wider than 3D14, and a line the 2D window cannot hold is broken again at runtime while
it is typed out (the writer only sees the revealed prefix: the word starts on one line and jumps
to the next, and the extra line pushes the message out of its box; user report 2026-10-16).
"""
from __future__ import annotations

import os
import re
import threading

from . import boxfit, bubble
from .metrics import FontSet, avg_char_width, line_widths, page_lines, wrap, wrap_pages, wrap_paged
from .textcodec import encode_str

MESSAGE_FONT, MENU_FONT = "3D14", "3D12"
MESSAGE_CATEGORIES = {"NPC Dialogue", "Event & Cutscene Dialogue", "Party Chat", "Battle Text",
                      "Shops & Services"}
CATEGORY_FONTS = {"Credits": "credit2D"}
# text pane of the 3D message window (win_talk_3d txt1: 350x66, three lines). Battle messages
# are left out: they are shown in other windows.
MESSAGE_WINDOW = 321      # text starts 10 px inside the frame (x 39); the same margin on the right
DIALOGUE_CATEGORIES = MESSAGE_CATEGORIES - {"Battle Text"}
# The message window shows at most 3 lines per message string ({CR} only pauses; a 4th line
# scrolls the first one out of view, a text after a {CR} on the 3rd line is not shown at all).
# The game also copies every message into a buffer of 256 UTF-16 units (0x1EFC8C, window
# object +0xC7C): a longer message is cut off and the window stays blank.
MESSAGE_LINES = 3
MESSAGE_MAX_UNITS = 255
# Menu message tables the game shows in the talk window (win_talk_3d, MESSAGE_FONT): field use
# of items, "can't use that here", camp menu messages. Their Japanese is at most 2 lines, so the
# table-based estimate gave a 2-line box narrower than the window (verified in game 2026-10-15:
# 3 lines of 321 px, no runtime wrap).
TALK_WINDOW_TABLES = frozenset({"mainmenu_msg.bxon", "item_msg.bxon", "item_failure_msg.bxon"})


def _talk_table(e) -> bool:
    return e["kind"] == "bxon" and "entry" in e.keys() and e["entry"] in TALK_WINDOW_TABLES
# 2D-mode message window: (font, line width). win_talk_2d widened by export.TALK_WIDE_2D: the
# writer's line width there is 372.0 at scale 1.0, no extra spacing (GDB in the 2D church talk,
# 2026-10-16), glyphs of 2D14 (a drawn line matched 2D14 to 2 px, not 2D14SH). 2way mode shows
# dialogue in the 3D window. Costs ~4% of the 3D line; 2 more of 28911 dialogue strings overflow.
# 2026-10-17: 376 (8 px margins in the 392 px frame) so that every dialogue line fits; the lower
# screen talk window (export.TALK_LOWER_2D, 2way after D-pad walking) uses the same width, condensed.
MESSAGE_2D: tuple = (("2D14", 376),)

# The help window next to the camp menu and its submenus (win_camp_text / win_text in the menu
# packs: 3 lines, text pane 100-102 x 50 at font size 13 in 3D12; 90 x 40 in 2D9 in 2D mode).
# Its texts are the multi-line entries of these tables. The window is shared, so a size set on
# one help text widens it for all of them; the game breaks lines that are too wide in the middle
# of a word. The exporter widens the text panes and frames of HELP_LAYOUTS by the same factor.
HELP_TABLES = {"mainmenu_text.bxon", "record_text.bxon"}
HELP_LAYOUTS = ("win_text_2d", "win_camp_text_2d", "win_text_3d", "win_camp_text_3d")
HELP_PANE = (100.0, 13.0)       # narrowest text pane (width, font size x), font MENU_FONT
HELP_LINES = 3
HELP_MAX_FACTOR = 2.0           # the frame then still ends inside the 320px lower screen

# Name tables whose entries are shown in fixed runtime panes (not in the table's own layout):
# entry -> (lines, [(font, pane width)...]). Monster names: the battle target label is
# 2 lines of 90 px (win_target_3d, 3D12) / 66 px (win_target_2d, 2D9); one-line fields (enemy
# list 150 px, drop lists 145 px, bestiary) join the two lines (export.name_break).
# The width allows the exporter to narrow the target label's glyphs to 0.8 (90 / 0.8 = 112).
# Monster names: 2 lines of the battle target label (112 px). The exporter narrows the glyphs of
# that label (and of the other name panes, export.RUNTIME_FIT) to the widest name, down to
# export.MIN_RUNTIME_SQUEEZE = 0.75, so a line may be up to 112 / 0.75 = 149 px (user's choice
# 2026-10-13: official names with one squeeze per pane rather than abbreviations).
NAME_FIELDS = {"enemy_base.bxon": (2, [("3D12", 148)])}
NAME_WRAP = 112     # names still break into two lines at the label's real width

# Layout labels may be up to 1/LABEL_SQUEEZE times their pane width: the exporter narrows
# their glyphs to fit (never below export.ExportOptions.min_label_squeeze).
LABEL_SQUEEZE = 0.8
# Item descriptions (three lines) are drawn in the item windows' text panes: 116 px at font size
# 13 (3D) and 106 px at font size 15 (2D). export.PANE_GEOMETRY squeezes those panes' glyphs to
# the factors below, so a line may be as wide as the squeezed pane: (pane px, font size x, squeeze).
ITEM_DESC_PANES = {"3d": (116.0, 13.0, 0.75), "2d": (106.0, 15.0, 0.6)}


def item_description_width(font) -> int:
    """Widest line of an item description in the source font's pixels: the tighter of the two panes."""
    return int(min(w * font.width / size / sq for w, size, sq in ITEM_DESC_PANES.values()))
# tables whose strings the game also puts into layout panes that show the same text: a string
# with layout occurrences is measured by its panes only
LABEL_TABLES = {"ui_text.bxon"}

_lock = threading.Lock()
_cache: dict[str, "GameFonts"] = {}


def for_project(project) -> "GameFonts":
    with _lock:
        g = _cache.get(project.db_path)
        if g is None:
            g = _cache[project.db_path] = GameFonts(project)
        return g


class GameFonts:
    def __init__(self, project):
        self.project = project
        self._layout: dict[tuple, str] = {}       # (file, member, pane section) -> font
        self._pane: dict[tuple, tuple] = {}       # (file, member, pane section) -> (width x scale, font size x)
        self._packs: set[str] = set()
        self._source: dict[int, str] | None = None
        self._help: set[int] | None = None
        self._paged: set[int] | None = None
        self._split: set[int] | None = None
        self._names: dict[int, tuple] | None = None
        self._labels: set[int] | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- which font
    def _load_pack(self, path: str):
        from .bflyt import Bflyt
        from .darc import Sarc
        from .pack import Pack
        self._packs.add(path)
        local = os.path.join(self.project.workdir, "extracted", "romfs", path.lstrip("/"))
        try:
            if os.path.isfile(local):
                with open(local, "rb") as fp:
                    data = fp.read()
            else:
                from .ctr.formats import open_image
                f, _, ncch = open_image(self.project.rom_path)
                try:
                    r = ncch.romfs()
                    data = r.read_file(next(x for x in r.files if x.path == path))
                finally:
                    f.close()
            for e in Pack.parse(data).entries:
                if e.data[:4] != b"SARC":
                    continue
                sarc = Sarc.parse(e.data)
                for n in sarc.nodes:
                    md = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                    if md[:4] != b"FLYT":
                        continue
                    lay = Bflyt(md)
                    for p in lay.text_panes():
                        if 0 <= p.font_index < len(lay.fonts):
                            self._layout[(path, n.name, p.index)] = lay.fonts[p.font_index].rsplit(".", 1)[0]
                        self._pane[(path, n.name, p.index)] = (p.width * p.scale[0], p.font_size[0])
        except Exception:
            pass

    def _pack_info(self, e):
        with self._lock:
            if e["file"] not in self._packs:
                self._load_pack(e["file"])
        key = (e["file"], e["member"], e["loc"])
        return self._layout.get(key), self._pane.get(key)

    def entry_font(self, e) -> str:
        """Font of one occurrence (an entry row: kind, file, member, loc, category)."""
        if e["kind"] == "bflyt":
            return self._pack_info(e)[0] or MENU_FONT
        if e["category"] in MESSAGE_CATEGORIES or _talk_table(e):
            return MESSAGE_FONT
        return CATEGORY_FONTS.get(e["category"], MENU_FONT)

    def source_font(self, sid: int) -> str:
        """Font to fit a string into: the font of its occurrences, the menu font when they
        disagree (the wider one)."""
        if self._source is None:
            fonts: dict[int, set] = {}
            for e in self.project.db().execute("SELECT source_id, kind, file, entry, member, loc, category FROM entry"):
                fonts.setdefault(e["source_id"], set()).add(self.entry_font(e))
            self._source = {sid: (next(iter(fs)) if len(fs) == 1 else MENU_FONT) for sid, fs in fonts.items()}
        if sid in self.bubbles():
            return bubble.FONT
        return self._source.get(sid, MENU_FONT)

    def bubbles(self) -> set[int]:
        """Sources shown only in the speech bubbles over NPC heads (bubble.py)."""
        if getattr(self, "_bubbles", None) is None:
            self._bubbles = bubble.only_sources(self.project)
        return self._bubbles

    def talk_only(self, sid: int) -> bool:
        """Every occurrence is in a TALK_WINDOW_TABLES table (shown in the talk window)."""
        if getattr(self, "_talk", None) is None:
            ok: dict[int, bool] = {}
            for e in self.project.db().execute("SELECT source_id, kind, entry FROM entry"):
                ok[e["source_id"]] = ok.get(e["source_id"], True) and _talk_table(e)
            self._talk = {k for k, v in ok.items() if v}
        return sid in self._talk

    def paged(self, sid: int) -> bool:
        """Shown only in the scrolling message window (dialogue): pages of MESSAGE_LINES lines,
        as many as needed."""
        if self._paged is None:
            ok: dict[int, bool] = {}
            for e in self.project.db().execute("SELECT source_id, kind, file, entry, member, loc, category FROM entry"):
                d = e["kind"] == "bxon" and e["category"] in DIALOGUE_CATEGORIES and self.entry_font(e) == MESSAGE_FONT
                ok[e["source_id"]] = ok.get(e["source_id"], True) and d
            self._paged = {sid for sid, v in ok.items() if v}
        return sid in self._paged

    def splittable(self, sid: int) -> bool:
        """Only used as talk lines (CharacterIEData: npc_ie / book_ie / script ie.bxon): the
        export can turn the text into several message boxes at {PAGE} (ietalk.py)."""
        if self._split is None:
            ok: dict[int, bool] = {}
            for e in self.project.db().execute("SELECT source_id, kind, entry, label FROM entry"):
                d = e["kind"] == "bxon" and e["label"] in ("CharacterIEData", "TalkPartyContent2")
                ok[e["source_id"]] = ok.get(e["source_id"], True) and d
            self._split = {sid for sid, v in ok.items() if v}
        return sid in self._split

    def name_field(self, sid: int):
        """(lines, [(font name, width)]) for strings of NAME_FIELDS tables, else None."""
        if self._names is None:
            self._names = {}
            q = ",".join("?" * len(NAME_FIELDS))
            for r in self.project.db().execute(
                    f"SELECT source_id, entry FROM entry WHERE kind = 'bxon' AND entry IN ({q})", tuple(NAME_FIELDS)):
                self._names[r[0]] = NAME_FIELDS[r[1]]
        return self._names.get(sid)

    # ---------------------------------------------------------------- how much room
    def _fonts(self):
        return FontSet.for_project(self.project).fonts

    def font(self, sid: int):
        fonts = self._fonts()
        return fonts.get(self.source_font(sid)) or fonts[MENU_FONT]

    def window_width(self, e) -> int | None:
        """Width of the window an occurrence is drawn in, in pixels of its font at 1:1, when it
        is known: the layout pane (glyphs are drawn at font size / FINF width and pane scale,
        a few panes use a smaller font size), or the message window. None: estimate it."""
        if e["kind"] == "bflyt":
            name, pane = self._pack_info(e)
            if not pane:
                return None
            width, size_x = pane
            font = self._fonts().get(name or MENU_FONT)
            if not (font and size_x > 0):
                return int(width)
            return int(width * font.width / size_x)
        if (e["category"] in DIALOGUE_CATEGORIES or _talk_table(e)) and self.entry_font(e) == MESSAGE_FONT:
            return MESSAGE_WINDOW
        if "source_id" in e.keys() and e["source_id"] in self.help_texts():
            return self.help_real()
        return None

    # ---------------------------------------------------------------- shared help window
    def help_real(self) -> int:
        """Width of the unchanged help window in MENU_FONT pixels."""
        font = self._fonts()[MENU_FONT]
        return int(HELP_PANE[0] * font.width / HELP_PANE[1])

    def help_texts(self) -> set[int]:
        """Source ids shown in the help window: 2-3 line entries of HELP_TABLES (no {v:..}
        values) whose Japanese fits the window; longer ones are shown elsewhere."""
        if self._help is None:
            font, real = self._fonts()[MENU_FONT], self.help_real()
            q = ",".join("?" * len(HELP_TABLES))
            rows = self.project.db().execute(
                f"SELECT DISTINCT s.id, s.text FROM entry e JOIN source s ON s.id = e.source_id"
                f" WHERE e.kind = 'bxon' AND e.entry IN ({q})", tuple(HELP_TABLES)).fetchall()
            self._help = {r[0] for r in rows if "{v:" not in r[1]
                          and 2 <= len(lw := line_widths(r[1], font)) <= HELP_LINES and max(lw) <= real}
        return self._help

    def help_width(self) -> int:
        """Help window width after export: the widest size set on any help text."""
        real = self.help_real()
        ids = sorted(self.help_texts())
        if not ids:
            return real
        r = self.project.db().execute(
            f"SELECT MAX(box_width) FROM source WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()
        return int(min(max(real, r[0] or 0), real * HELP_MAX_FACTOR))

    def help_factor(self) -> float:
        return self.help_width() / self.help_real()

    def _box(self, sid: int):
        return boxfit.index(self.project, self.font(sid), self.window_width)

    def _extra(self, sid: int) -> list[tuple]:
        """Further (font, width) limits: the 2D windows for message text, the other panes of
        NAME_FIELDS names."""
        nf = self.name_field(sid)
        if nf:
            fonts = self._fonts()
            return [(fonts[n], w) for n, w in nf[1][1:] if n in fonts]
        if self.source_font(sid) != MESSAGE_FONT:
            return []
        fonts = self._fonts()
        return [(fonts[n], w) for n, w in MESSAGE_2D if n in fonts]

    def custom_box(self, sid: int) -> tuple:
        """(width, lines) set by hand in the editor; None where automatic."""
        r = self.project.db().execute("SELECT box_width, box_lines FROM source WHERE id=?", (sid,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def label_only(self, sid: int) -> bool:
        """Shown only as the text of layout panes of known width (not field map labels): the
        exporter narrows the glyphs of a label that is too wide (export: min_label_squeeze)."""
        if self._labels is None:
            import re
            mapl = re.compile(r"(^|/)[bdfm]\d{3}_")
            ok: dict[int, bool] = {}
            has_layout: set[int] = set()
            for e in self.project.db().execute("SELECT source_id, kind, file, entry, member, loc, category FROM entry"):
                if e["kind"] == "bxon" and e["entry"] in LABEL_TABLES:
                    continue        # the table the game fills these layout panes from at runtime
                good = e["kind"] == "bflyt" and not mapl.search(e["member"] or "") and bool(self.window_width(e))
                ok[e["source_id"]] = ok.get(e["source_id"], True) and good
                if good:
                    has_layout.add(e["source_id"])
            self._labels = {k for k, v in ok.items() if v and k in has_layout}
        return sid in self._labels

    def _item_descriptions(self) -> set[int]:
        """Strings of the item table: their three-line descriptions go into the squeezed item panes."""
        if getattr(self, "_itemdesc", None) is None:
            self._itemdesc = {r[0] for r in self.project.db().execute(
                "SELECT DISTINCT source_id FROM entry WHERE entry = 'item.bxon'")}
        return self._itemdesc

    def get(self, sid: int, source: str) -> dict:
        box = self._box(sid)
        lim = dict(box.get(sid, source), auto_width=None, auto_lines=None)
        font = box.font
        width, lines = lim["width"], lim["lines"]
        cw, cl = self.custom_box(sid)
        if not cw and self.label_only(sid):
            width = int(width / LABEL_SQUEEZE)
        if lines == 3 and not cw and sid in self._item_descriptions():
            width = item_description_width(font)     # squeezed pane, see ITEM_DESC_PANES
        help = sid in self.help_texts()
        if help:
            # one window for all help texts: the widest size set on any of them; no more lines
            hw, lines = self.help_width(), min(lines, HELP_LINES)
            if cw or cl or hw != width:
                lim.update(auto_width=width, auto_lines=lines)
            width, lines = hw, min(cl or lines, HELP_LINES)
        elif cw or cl:
            lim.update(auto_width=width, auto_lines=lines)
            width, lines = cw or width, cl or lines
        nf = None if (cw or cl) else self.name_field(sid)
        if nf:
            lines, (fname, fw) = nf[0], nf[1][0]
            font = self._fonts().get(fname, font)
            width = fw
            lim.update(auto_width=width, auto_lines=lines)
        if not (cw or cl or nf or help) and self.talk_only(sid):
            width, lines = MESSAGE_WINDOW, MESSAGE_LINES
        paged = False     # one message string is one box of MESSAGE_LINES lines ...
        if not help and self.paged(sid):
            paged = self.splittable(sid)    # ... but NPC talk lines can become several boxes
            lines = min(lines, MESSAGE_LINES)
            lim["slot_chars"] = min(MESSAGE_MAX_UNITS, lim["slot_chars"] or MESSAGE_MAX_UNITS)
        lim.update(help_window=help, help_max=int(self.help_real() * HELP_MAX_FACTOR) if help else None,
                   paged=paged)
        fit_w = min([width] + [w * avg_char_width(font) / avg_char_width(f) for f, w in self._extra(sid)])
        per_line = max(3, int(fit_w / avg_char_width(font)))
        budget = per_line if lines < 2 else int(per_line * lines * 0.85)   # wrapping never fills every line
        if lim["slot_chars"] is not None:
            budget = min(budget, lim["slot_chars"])
        if sid in self.bubbles():
            width, lines = bubble.PANE_WIDTH, bubble.LINES
            per_line = max(3, int(width / avg_char_width(font)))
            budget = int(per_line * lines * 0.85)
            lim.update(paged=False, slot_chars=None, bubble=True)
        lim.update(width=width, lines=lines, chars_per_line=per_line, max_chars=budget)
        return lim

    def fits(self, sid: int, source: str, translation: str) -> bool:
        return self.overshoot(sid, source, translation) == 0

    def overshoot(self, sid: int, source: str, translation: str) -> float:
        """0 when it fits; otherwise relative excess width plus extra lines."""
        lim = self.get(sid, source)
        flat = translation.replace("{PAGE}", "\n")
        tw = line_widths(flat, self.font(sid))
        pages = translation.split("{PAGE}") if lim["paged"] else [translation]
        extra_lines = sum(max(0, p.strip("\n").count("\n") + 1 - lim["lines"]) for p in pages)
        o = max(0, max(tw) - lim["width"]) / lim["width"] + extra_lines
        if lim["slot_chars"] is not None and max(len(encode_str(p)) for p in pages) > lim["slot_chars"]:
            o += 1
        for f, w in self._extra(sid):
            o += max(0, max(line_widths(flat, f)) - w) / w
        return o

    def _wrap_name(self, sid: int, translation: str, width: int) -> str:
        """One line when it fits the label's real width (NAME_WRAP px), else the two-line split
        whose wider line is narrowest. `width` (the squeezed limit) only decides what fits."""
        font = self._fonts().get(self.name_field(sid)[1][0][0]) or self.font(sid)
        words = [w for w in translation.replace("\n", " ").split(" ") if w]
        one = " ".join(words)
        if line_widths(one, font)[0] <= min(width, NAME_WRAP) or len(words) < 2:
            return one
        best = min(range(1, len(words)), key=lambda i: max(line_widths(" ".join(words[:i]), font)[0],
                                                       line_widths(" ".join(words[i:]), font)[0]))
        return " ".join(words[:best]) + "\n" + " ".join(words[best:])

    def _battle_texts(self) -> set[int]:
        """Strings of battle_Text.bxon (battle messages, reflowed by export.battle_reflow)."""
        if getattr(self, "_battle", None) is None:
            self._battle = {r[0] for r in self.project.db().execute(
                "SELECT DISTINCT source_id FROM entry WHERE entry = 'battle_Text.bxon'")}
        return self._battle

    def wrap(self, sid: int, source: str, translation: str) -> str:
        """Line breaks for the window(s) the string is shown in."""
        if sid in self._battle_texts() and "{PAGE}" not in translation:
            # every sentence on lines of its own: the export keeps only the breaks after a
            # sentence (export.battle_reflow), so a break wrapped across two sentences was
            # joined and the runtime wrap re-broke the message mid-sentence ("...gains 6
            # experience / points! 6 gold received!", user 2026-10-25)
            parts = re.split(r"(?<=[.!?…)'\"]) *\n *", translation)
            if len(parts) > 1:
                return "\n".join(self._wrap(sid, source, p) for p in parts)
        return self._wrap(sid, source, translation)

    def _wrap(self, sid: int, source: str, translation: str) -> str:
        if sid in self.bubbles():
            return bubble.layout(translation, self.font(sid))
        lim = self.get(sid, source)
        if self.name_field(sid) and lim["lines"] == 2:
            return self._wrap_name(sid, translation, lim["width"])
        if lim["paged"]:
            return wrap_pages(translation, source, self.font(sid), lim["width"], lim["lines"],
                              max_units=lim["slot_chars"], extra=self._extra(sid))
        return wrap(translation, source, self.font(sid), width=self.get(sid, source)["width"],
                    extra=self._extra(sid))
