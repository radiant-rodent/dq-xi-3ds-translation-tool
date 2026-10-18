"""Apply translations from a project to the game files and build output images."""
from __future__ import annotations

import os
import re
import shutil
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import blz, cnut, codefix, codestr, fontimport, gamefont, images
from .bflyt import Bflyt
from . import ietalk
from .bxon import Bxon
from .ctr.build import build_image, verify_image
from .ctr.formats import open_image
from .darc import Sarc
from .pack import Pack
from .project import Project
from .textcodec import encode_str, tags_of


@dataclass
class ExportOptions:
    include_drafts: bool = True          # export machine/draft translations too
    expand_text_buffers: float = 2.0     # multiply UI text box buffers (1 = off)
    # Minimum UI text buffer in bytes (0 = off). Almost every text box is below 64 bytes, so a
    # minimum grows all of them and overflows the game's layout heap (crash when entering the
    # first field area); x2 alone was tested to be safe.
    min_text_buffer: int = 0
    # Minimum buffer (bytes) of the text panes that are not numbers: a party name set directly
    # (txt_name, list items) is expanded in the pane's buffer (namefix.py); 22 = 10 units + NUL
    name_buffer: int = 22
    font_fixes: bool = True              # narrow over-wide ASCII glyph advances in fonts
    make_3ds: bool = True
    make_layeredfs: bool = True
    make_cia: bool = False
    verify: bool = True
    keep_update_partitions: bool = True
    include_script_labels: bool = False  # Squirrel string literals are lookup keys, not on-screen text
    latin_keyboard: bool = True          # name entry: A-Z a-z keyboard (+ accented letters with the Latin font)
    # The big menu packs (item menu etc.) load thousands of tiny text boxes at once; growing them
    # all x2 overflowed the game's layout heap (crash on opening the item menu). They keep the
    # original buffers unless a tested factor is set here.
    menu_buffer_factor: float = 1.0
    # bytes moved from an underused heap to the UI layout heap (memfix.py): English text boxes
    # need more vertex memory than Japanese ones; without it the item details screens ran out
    # of memory (crash)
    ui_heap_extra: int = 0x100000
    main_heap_extra: int = 0x100000     # memfix.patch_main: room for the longer English NPC files
    word_wrap: bool = True              # wordwrap.patch: runtime text breaks at spaces
    grammar: bool = True                # grammar.patch: articles / plurals resolved in game
    # layout labels wider than their text pane are drawn with narrower glyphs (font size x)
    # down to this factor, instead of running over their frame
    min_label_squeeze: float = 0.6
    # autofit.patch: one-line labels (panes marked by the export, see FIT_EXCLUDE) are narrowed
    # at runtime, string by string, when wider than their pane, and never wrap
    runtime_fit: bool = True
    runtime_fit_multi: bool = True      # ... and multi-line boxes are scaled down to their height
    test_input_hook: bool = False        # emulator tests only: RPC "mailbox" input (testhook.py)
    test_probes: tuple = ()              # emulator tests only: debug probes (testhook.PROBES)
    test_text_log: bool = False          # emulator tests only: log every pane text (textlog.py)
    # tests only: in MENU_PACKS, apply the buffer factor to these (pack, layout member) pairs only
    expand_only: frozenset | None = None
    expand_panes: frozenset | None = None   # tests only: ... and only these text panes there
    # minimum text buffer (bytes) of the message window panes (win_talk_* layouts): the game
    # does not grow these at runtime, so a page longer than the file buffer renders blank
    talk_buffer: int = 0
    # lines of the 3D message window: 4 = one line more than the Japanese game; the window
    # grows upwards by one line (TALK_GROW), its bottom edge stays where it was
    talk_lines: int = 3
    # tests: (font size y, line space) for the message window text panes (win_talk_*)
    talk_style: tuple | None = None
    # the 2D-mode message window on the top screen nearly as wide as the screen (TALK_WIDE_2D)
    talk_wide_2d: bool = True
    # skill / spell descriptions (REFLOW_FILES) without their line breaks: the runtime word
    # wrap (wordwrap.py) breaks them at spaces for each window (battle help ~214 px, Skill
    # Panel 136 px); wrapped for the widest one they broke into 5 ragged lines on the panel
    reflow_descriptions: bool = True
    # battle messages (battle_Text) without the line breaks their Japanese has not: they came
    # from the PC text, set for its 3D window; the 2D battle box (338 px of 2D14) broke those
    # lines again ("...blocks the attack with / their / shield!"). The runtime wrap balances them
    # for the window they are shown in (battle_reflow)
    reflow_battle: bool = True
    # Japanese text drawn into textures redrawn in English (texlabel.py)
    texture_labels: bool = True
    # title logo with the official English subtitle (logo.py, from dq11tool/data/logo_en.png)
    title_logo: bool = True
    # field map place labels: one English name per label instead of one per fragment (maplabel.py)
    map_labels: bool = True


@dataclass
class ExportReport:
    applied: int = 0
    skipped: list = field(default_factory=list)       # (entry id, reason)
    warnings: list = field(default_factory=list)
    files_changed: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    seconds: float = 0.0

    def as_dict(self):
        return {
            "applied": self.applied,
            "skipped": self.skipped[:500],
            "skipped_count": len(self.skipped),
            "warnings": self.warnings[:500],
            "warning_count": len(self.warnings),
            "files_changed": len(self.files_changed),
            "outputs": self.outputs,
            "seconds": round(self.seconds, 1),
        }


def validate(source: str, translation: str, official: bool = False) -> list[str]:
    """official: the line is from the official script, which often leaves out the hero's name
    {v:1} where the Japanese uses it (harmless: the game just prints nothing for it)."""
    problems = []
    st, tt = tags_of(source), tags_of(translation)
    missing = [t for t in st if t not in tt and not (official and t == "{v:1}")]
    extra = [t for t in tt if t not in st]
    if missing:
        problems.append("missing tags: " + " ".join(sorted(set(missing))))
    if extra:
        problems.append("unknown tags: " + " ".join(sorted(set(extra))))
    if translation.count("{") - translation.count("{{") * 2 != translation.count("}") - translation.count("}}") * 2:
        problems.append("unbalanced braces (use {{ and }} for literal braces)")
    if "ヰ" in source and "ヰ" not in translation:
        problems.append("keep ヰ: the game font draws it as the 'E' (equipped) icon")
    if UNRESOLVED_PC.search(translation):
        problems.append("unresolved PC markup (" + ", ".join(sorted(set(UNRESOLVED_PC.findall(translation)))[:3])
                        + "): rephrase or let the auto-translator fix it")
    return problems


# PC (Steam) markup such as <pc> or <LM_DefSgl_Target> that has no 3DS equivalent in this line
UNRESOLVED_PC = re.compile(r"<[A-Za-z_][^<>]*>")


def collect_changes(project: Project, opts: ExportOptions, progress=None):
    """Returns (romfs_replacements, exefs_replacements, report, modified_code_bytes)."""
    progress = progress or (lambda *a: None)
    report = ExportReport()
    db = project.db()
    min_status = 1 if opts.include_drafts else 2
    rows = db.execute(
        "SELECT e.id, s.id AS sid, e.kind, e.file, e.entry, e.member, e.loc, e.capacity, e.category, s.text AS src,"
        " COALESCE(NULLIF(e.override, ''), s.translation) AS tr, s.status, s.box_width, s.origin"
        " FROM entry e JOIN source s ON s.id = e.source_id"
        " WHERE (e.override IS NOT NULL AND e.override != '') OR"
        " (s.translation IS NOT NULL AND s.translation != '' AND s.status >= ?)", (min_status,)).fetchall()

    by_file: dict[str, list] = defaultdict(list)
    from .metrics import FontSet, fold_to_font, line_widths
    game_font = FontSet.for_project(project).fonts["3D14"]
    folded = []
    for r in rows:
        r = dict(r)
        r["tr"] = fold_to_font(r["tr"], game_font)   # no accents/hearts in the game fonts
        if r["kind"] == "bxon":
            r["tr"] = name_break(r["src"], r["tr"])
        if opts.reflow_descriptions and is_description(r):
            r["tr"] = re.sub(r" *\n *", " ", r["tr"])
        elif opts.reflow_battle:
            r["tr"] = battle_reflow(r)
        folded.append(r)
    rows = folded
    # layout panes given a wider box in the editor are widened by the same factor
    gf = gamefont.for_project(project)
    for r in rows:
        r["pane_scale"] = 1.0
        if r["kind"] == "bflyt" and r["box_width"]:
            real = gf.window_width(r)
            if real and r["box_width"] > real:
                r["pane_scale"] = r["box_width"] / real
        r["squeeze"] = 1.0
        if r["kind"] == "bflyt" and r["pane_scale"] == 1.0 and opts.min_label_squeeze < 1:
            real = gf.window_width(r)
            font = gf._fonts().get(gf.entry_font(r))
            if real and font:
                w = max(line_widths(r["tr"], font))
                if w > real:
                    r["squeeze"] = max(opts.min_label_squeeze, real / w)
    # the shared help window, widened to the widest size set on a help text
    help_factor = gf.help_factor()
    for r in rows:
        # official PC lines often leave out the hero's name {v:1} (harmless; the editor's rule)
        probs = validate(r["src"], r["tr"], official=r["origin"] == "pc")
        for p in probs:
            report.warnings.append({"entry": r["id"], "text": r["src"][:60], "problem": p})
        if UNRESOLVED_PC.search(r["tr"]):          # never write "<pc>" into the game
            report.skipped.append({"entry": r["id"], "text": r["src"][:60], "reason": "unresolved PC markup"})
            continue
        by_file[r["file"]].append(r)
    # party names longer than the save's name field: a code the grammar stub expands (namefix.py)
    name_codes = {}
    if opts.grammar:
        from . import namefix
        pi = [r for rs in by_file.values() for r in rs if r.get("entry") == namefix.INIT_TABLE]
        name_codes = namefix.assign([r["tr"] for r in pi])
        for r in pi:
            r["tr"] = name_codes.get(r["tr"], r["tr"])

    f, ncsd, ncch = open_image(project.rom_path)
    romfs_repl: dict[str, bytes] = {}
    exefs_repl: dict[str, bytes] = {}
    code_out = None
    try:
        romfs = ncch.romfs()
        files = {x.path: x for x in romfs.files}

        override_root = os.path.join(project.workdir, "override", "romfs")
        overrides = {}
        if os.path.isdir(override_root):
            for dp, dn, fn in os.walk(override_root):
                for n in fn:
                    full = os.path.join(dp, n)
                    rel = "/" + os.path.relpath(full, override_root).replace(os.sep, "/")
                    if rel in files:
                        overrides[rel] = full
                    else:
                        report.warnings.append({"entry": None, "text": rel,
                                                "problem": "override file does not exist in the game RomFS (ignored)"})

        # which packs need a rebuild: translated ones + all layout packs (buffer expansion)
        layout_packs = set()
        if opts.expand_text_buffers > 1 or opts.min_text_buffer:
            layout_packs = set(project_meta(project, "layout_packs", []))
        help_packs = {p for p in files if os.path.basename(p) in MENU_PACKS} if help_factor > 1 else set()
        targets = sorted((set(by_file) - {"exefs:.code"}) | layout_packs | help_packs)
        runtime = (runtime_names(rows), gf._fonts())

        for i, path in enumerate(targets):
            progress("Applying translations", i, len(targets))
            if path in overrides:
                report.warnings.append({"entry": None, "text": path,
                                        "problem": "file has an override; text changes for it were not applied"})
                continue
            rf = files.get(path)
            if rf is None:
                continue
            original = romfs.read_file(rf)
            new = _patch_pack(original, by_file.get(path, []), opts, report, path,
                              help_factor if path in help_packs else 1.0, runtime)
            if new is not None:
                romfs_repl[path] = new
                report.files_changed.append(path)
        progress("Applying translations", len(targets), len(targets))

        for path, full in overrides.items():
            with open(full, "rb") as fp:
                romfs_repl[path] = fp.read()
            report.files_changed.append(path + " (override)")

        # fonts: narrow Latin glyphs (bundled DQ VII font), width fixes and composed
        # accented letters
        cache_dir = os.path.join(project.workdir, "cache")
        for path in files:
            if path.startswith("/font/") and path.endswith(".bffnt") and path not in overrides:
                data = romfs.read_file(files[path])
                name = path.rsplit("/", 1)[-1][:-6]
                patched = fontimport.build(name, data, opts.font_fixes, cache_dir)
                if patched != data:
                    romfs_repl[path] = patched
                    report.files_changed.append(path + (" (Latin font)" if name in fontimport.TARGET_FONTS
                                                        else " (font fix)"))

        # Japanese text inside layout images (Draconian Quest badges, "LEVEL UP!")
        badge_font = romfs_repl.get("/font/2D14.bffnt") or romfs.read_file(files["/font/2D14.bffnt"])
        for path in files:
            if not (path.startswith("/ui/") and path.endswith(".pack")) or path in overrides:
                continue
            if path.rsplit("/", 1)[-1] not in images.PACKS:
                continue
            data = romfs_repl.get(path) or romfs.read_file(files[path])
            new = images.patch_pack(data, badge_font)
            if new is not None:
                romfs_repl[path] = new
                if path not in report.files_changed:
                    report.files_changed.append(path + " (images)")

        # title screen logo with the official English subtitle (logo.py; needs data/logo_en.png)
        if opts.title_logo:
            from . import logo
            for path in files:
                if path.rsplit("/", 1)[-1] in logo.PACKS and path not in overrides:
                    new = logo.patch_pack(romfs_repl.get(path) or romfs.read_file(files[path]))
                    if new is not None:
                        romfs_repl[path] = new
                        report.files_changed.append(path + " (English logo)")

        # Japanese text drawn into textures: town banners, map hints, quest status badges
        if opts.texture_labels:
            from . import texlabel
            tfont = romfs_repl.get(f"/font/{texlabel.FONT}.bffnt") or romfs.read_file(files[f"/font/{texlabel.FONT}.bffnt"])
            for path in files:
                if not (path.startswith("/ui/") and path.endswith(".pack")) or path in overrides:
                    continue
                if path.rsplit("/", 1)[-1] not in texlabel.PACKS:
                    continue
                data = romfs_repl.get(path) or romfs.read_file(files[path])
                new = texlabel.patch_pack(data, tfont)
                if new is not None:
                    romfs_repl[path] = new
                    if path not in report.files_changed:
                        report.files_changed.append(path + " (images)")
            banners = {p.rsplit("/", 1)[-1]: p for p in files
                       if p.startswith("/ui/location/") and p.endswith(".bch") and p not in overrides}
            new = texlabel.patch_banners({n: romfs.read_file(files[p]) for n, p in banners.items()}, tfont)
            for n, d in new.items():
                romfs_repl[banners[n]] = d
                report.files_changed.append(banners[n] + " (town banner)")

        # executable strings
        code_rows = by_file.get("exefs:.code", [])
        exheader = ncch.exheader()
        compressed = bool(exheader[0x0D] & 1)
        code = ncch.exefs().read(".code")
        if compressed:
            code = blz.decompress(code)
        new_code = code
        repl, moves, row_at = {}, {}, {}
        for r in code_rows:
            raw = encode_str(r["tr"])
            need = len(raw.encode("utf-16-le")) + 2
            row_at[r["loc"]] = r
            if need > r["capacity"]:
                moves[r["loc"]] = (raw, r["capacity"])    # moved into free space below
                continue
            repl[r["loc"]] = (raw, r["capacity"])
            report.applied += 1
        fmt_moves, fmt_problems = codefix.format_moves(code)   # e.g. "%ls %c" ("Slime A")
        moves.update(fmt_moves)
        if repl:
            new_code = codestr.patch(new_code, repl)
        if moves:
            new_code, failed = codestr.Mover(code, exheader).apply(new_code, moves)
            report.applied += len([m for m in moves if m in row_at]) - len([f for f in failed if f in row_at])
            for loc, why in failed.items():
                r = row_at.get(loc)
                if r is None:
                    fmt_problems.append(f"executable format at {loc + 0x100000:#x}: cannot move it: {why}")
                    continue
                report.skipped.append({"entry": r["id"], "text": r["src"][:60],
                                       "reason": f"too long for executable slot "
                                                 f"(max {r['capacity'] // 2 - 1} chars; cannot move it: {why})"})
        if opts.latin_keyboard:
            new_code = _latin_keyboard(new_code, romfs, files, romfs_repl, report)
        new_code, problems = codefix.patch(new_code, code)    # particles glued onto names
        new_code, ip = codefix.patch_instructions(new_code)
        problems += ip
        new_code, ip = codefix.patch_name_table(new_code, code)     # Save Code with Latin names
        problems += ip
        new_code, ip = codefix.patch_save_code(new_code, code)      # Latin Save Code alphabet
        problems += ip
        problems += fmt_problems
        if opts.word_wrap:
            from . import wordwrap
            new_code, ww = wordwrap.patch(new_code)
            problems += ww
        if opts.grammar:
            from . import grammar
            new_code, gp = grammar.patch(new_code)
            problems += gp
            from . import namefix
            new_code, gp = namefix.patch(new_code, name_codes)
            problems += gp
        if opts.runtime_fit:
            from . import autofit
            new_code, ap = autofit.patch(new_code)
            problems += ap
        if opts.ui_heap_extra:
            from . import memfix
            new_code = memfix.patch(new_code, opts.ui_heap_extra)
        if opts.main_heap_extra:
            from . import memfix
            new_code = memfix.patch_main(new_code, opts.main_heap_extra)
        report.warnings.extend({"entry": None, "text": "exefs:.code", "problem": p} for p in problems)
        if opts.test_input_hook:
            from . import testhook
            new_code = testhook.apply(new_code, opts.test_probes)
            if opts.test_text_log:
                from . import memfix, textlog
                new_code = textlog.patch(new_code)
                new_code = memfix.reserve_tail(new_code, textlog.RING_RESERVE)
        if new_code != code:
            code_out = new_code
            if compressed:
                c = blz.compress(code_out)
                if c is None:
                    raise RuntimeError("could not recompress .code")
                exefs_repl[".code"] = c
            else:
                exefs_repl[".code"] = code_out
            report.files_changed.append("exefs:.code")
    finally:
        f.close()
    return romfs_repl, exefs_repl, report, code_out


def _latin_keyboard(code: bytes, romfs, files, romfs_repl: dict, report: ExportReport) -> bytes:
    """Name entry: Latin keys in the executable's table and on the keyboard textures.
    The accent page is only used when the message font can draw accented letters."""
    from . import namekbd
    def final(name):
        p = f"/font/{name}.bffnt"
        return romfs_repl.get(p) or romfs.read_file(files[p])
    font = final("3D14")                        # keys are drawn with the message font
    ok = namekbd.supported({n: final(n) for n in namekbd.NAME_FONTS if f"/font/{n}.bffnt" in files})
    accents = namekbd.accent_page(ok) if any(c in ok for c in namekbd.ACCENT_LOWER) else None
    patched = namekbd.patch_code(code, accents)
    pack_path = namekbd.TITLE_PACK
    if patched is None or pack_path not in files:
        report.warnings.append({"entry": None, "text": "exefs:.code",
                                "problem": "name-entry keyboard not found; keyboard left unchanged"})
        return code
    pack = romfs_repl.get(pack_path) or romfs.read_file(files[pack_path])
    romfs_repl[pack_path] = namekbd.patch_title_pack(pack, font, accents)
    if pack_path + " (Latin keyboard)" not in report.files_changed:
        report.files_changed.append(pack_path + " (Latin keyboard)")
    return patched


def name_break(source: str, translation: str) -> str:
    """Two-line names ("イシの\nつるぎ") are shown on one line in lists, where the game drops
    the line break ("Cobblestonesword"). A space before the break keeps the words apart there
    and is invisible at the end of the first line in two-line fields."""
    # (also when only the English is two lines: "スライム" -> "Malicious\nAbominable Showman")
    if (source.count("\n") <= 1 and translation.count("\n") == 1 and "{CR}" not in source
            and len(re.sub(r"\{[^{}]*\}", "", source)) <= 16 and " \n" not in translation):
        return translation.replace("\n", " \n")
    return translation


def project_meta(project: Project, key: str, default=None):
    import json
    r = project.db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


TALK_LINE_PX = 22.0      # line pitch of the 3D message window (font height 16 + line space 6)
# panes moved by the talk window growth: (dy, dheight) per line added
TALK_GROW = {
    # the whole window moves up one line, its frame grows down by one line, the "next" arrow
    # stays at the bottom; text panes keep their places relative to each other
    "win_talk_3d": {"null_win_talk": (1, 0), "win": (0, 1), "null_icon_next": (-1, 0),
                    "null_win_talk_left": (1, 0), "null_icon_next_l": (-1, 0)},
    "win_talk_base_3d": {"null_win_talk_base": (1, 0), "win_bottom": (0, 1)},
}
# 2D mode, top screen: dialogue is wrapped for the 3D window (321 px of 3D14); the 2D pixel
# font 2D14 (drawn 1:1) is ~13% wider, so the 320 px window broke most lines again and the
# third line of a page went below the frame (user report 2026-10-14: "2D dialogues align
# worse than 3D"). A 372 px text pane holds 99.6% of the 3D lines (380: 99.97%): the window
# becomes 392 px (the screen is 400), the text keeps a 10 px margin on the left, the speaker
# name tab moves with the left edge. (layout -> ({pane: width}, {pane: dx}))
TALK_WIDE_2D = {
    "win_talk_2d": ({"win": 392.0, "txt": 376.0, "txt_l": 376.0, "txt_kanban_center": 376.0},
                    {"txt": -10.0, "txt_l": -10.0, "txt_name_l": -36.0}),
    # the frame of the speaker version (prt1 win_left in win_talk_2d)
    "win_talk_base_2d": ({"win_bottom": 392.0}, {"win_left1": -36.0}),
}
# battle_2d.pack has its own win_talk_2d (text pane 338 px at x 1 in a frame the battle sizes
# itself): the widening above moved its text onto the frame (user report 2026-10-16)
TALK_WIDE_SKIP_PACKS = ("/ui/battle_2d.pack",)
# 2D mode with the screens swapped (Y "Swap map"): the field and its talk window are on the
# 320 px lower screen (win_talk_lw_2d, text pane 300 px at x 2; common_2d and common_2way). Only
# 4% of the dialogue lines fit 300 px of 2D14 (median 347), so the game broke nearly every
# line again and boxes ran to a 4th line (user report 2026-10-16, "Welcome to Heliodor, /
# greatest city in all / Erdrea..."). The pane gets the 2D top window's line width and is
# narrowed horizontally to the old screen width: the pre-wrapped lines stay as they are.
TALK_LOWER_2D_WIDTH = 376.0       # = gamefont.MESSAGE_2D
TALK_LOWER_2D_SHOWN = 302.0       # px on screen: from the old left edge (x -148) to x 154
TALK_LOWER_2D = {"win_talk_lw_2d": ("txt", "txt_l", "txt_kanban_center")}
# Other pane geometry (layout -> ({pane: width}, {pane: dx})). Skill Panel (bottom screen):
# the description box was 120 px from x 15 (reflow_descriptions); the plate's frame starts at
# ~150, the left column ends at ~6
PANE_GEOMETRY = {
    "common_3d_lw_skill": ({"txt_pane": 136.0}, {"txt_pane": -5.0}),
    # Skill Panel tree totals ("Swordmastery: 0/ 7"): the right-aligned 96 px labels began 3 px
    # left of the window (x -152) and on its frame; 85 px keeps the right edge
    "menu_skill_get_3d": ({f"txt_{c}{k}": 85.0 for c in "lr" for k in (1, 2, 3)}, {}),
}


# descriptions only: the field menu's message window does not wrap at runtime (a reflowed
# item message ran off its right edge, 2026-10-15)
REFLOW_FILES = ("battle_act_text.bxon", "passive_skill.bxon")


def battle_reflow(r) -> str:
    """A battle message without the line breaks that only split a sentence
    (ExportOptions.reflow_battle): every {CR} break (the Japanese breaks its one-sentence
    messages that way), and plain breaks when the Japanese has none. The 3D captions (2D14SH,
    400 px), the 2D battle box (2D14, 338 px) and win_message_2d (2D9) all differ in width;
    the runtime word wrap balances the lines for each. Breaks between the sentences of a
    multi-line Japanese message ("...experience points!\n... gold received!") stay."""
    tr = r["tr"]
    if r.get("entry") != "battle_Text.bxon" or "{PAGE}" in tr or "\n" not in tr:
        return tr
    tr = re.sub(r" *\{CR\}\n *", " ", tr)
    if "+{v:" in tr:                       # the stat gains list: its lines stay
        return tr
    # a break stays only between two sentences ("...seaweed stuck in your teeth and you /
    # can't move!" ran past the 3D caption's 400 px, user 2026-10-17)
    return re.sub(r"(?<![.!?…)'\"]) *\n *", " ", tr)


def is_description(r) -> bool:
    """A sentence of REFLOW_FILES on several lines whose English the game may wrap itself
    (ExportOptions.reflow_descriptions)."""
    return (r.get("entry") in REFLOW_FILES and "\n" in r["tr"]
            and not re.search(r"\{(CR|PAGE)\}", r["tr"])
            and r["tr"].rstrip().endswith((".", "!", "?")))      # a sentence, not a two-line name


# The game scrolls the text up once the lines no longer fit (font height + line space) into
# 66 px, but draws the lines with a fixed pitch: a line space of 0.5 lets 4 lines count as
# fitting without changing how they look
TALK_STYLE = {"win_talk_3d": (15.0, 2.0)}   # experimental (talk_lines=4 is off by default)
# Panes the game fills at runtime with names from a table (they have no text of their own to
# fit): (layout, panes, table, how the name is shown, new pane width or None). The glyphs of
# the pane are narrowed so the longest translated name fits ("line": every line of a two-line
# name, "joined": the name on one line, "bullet": "・" + the name on one line).
RUNTIME_FIT = [
    # the target label floats over the monster (no frame): wider than the Japanese 90 px
    ("win_target_3d", ("txt_target",), "enemy_base.bxon", "line", 130.0),
    ("win_target_2d", ("txt_target",), "enemy_base.bxon", "line", 120.0),
    ("win_target_2d", ("txt_command",), None, "line", 100.0),         # skill names: width only
    # enemy list: the count column right of the names is one digit now (codefix "%d匹"), so the
    # names may use the room up to it (window 240 / 225 px, names start at x 25 / 21; the digit ends at x 226 / 214)
    ("win_enemy_3d", tuple(f"txt_l{i}" for i in range(1, 8)), "enemy_base.bxon", "joined", 185.0),
    ("win_enemy_2d", tuple(f"txt_l{i}" for i in range(1, 8)), "enemy_base.bxon", "joined", 185.0),
    ("item_details_3d", ("txt_monster",), "enemy_base.bxon", "bullet", None),
    ("item_details_2d", ("txt_monster",), "enemy_base.bxon", "bullet", None),
    # battle commands, right column (Abilities / Items / Equip, filled at runtime): 3D panes
    # widened from 50 to 54 px (right-aligned at x 144, still clear of the left column)
    ("win_command_3d", ("txt_r1", "txt_r2", "txt_r3"), (8049, 7633, 11540), "line", 54.0),
    # 2D: 40 -> 43 px (the text starts 3 px further left; "Attack" keeps 21..70 px of the left column)
    ("win_command_2d", ("txt_r1", "txt_r2", "txt_r3"), (8049, 7633, 11540), "line", 43.0),
    # the "Organise Items" button of the bag (mainmenu_text 41442); the X icon takes ~18 px
    ("win_dougu_sort2_3d", ("txt_title",), (41442,), "joined", None),
    ("win_dougu_sort2_2d", ("txt_title",), (41442,), "joined", None),
]
RUNTIME_RESERVE = {"win_dougu_sort2_3d": 18.0, "win_dougu_sort2_2d": 18.0}   # px not usable by text
# The 3D target label is drawn on the bottom screen centred at x 40 (the Japanese 90 px pane
# just fits); moved right so the 130 px pane stays on screen (found in battle 2026-10-13:
# "Malicious / Abominable Showman" was cut at the left edge)
RUNTIME_MOVE_X = {"win_target_3d": {"null_win_target": 25.0},
                  # 2D: "Vicious hard-boiled eggsoskeleton" was cut at the left edge (2026-10-17)
                  "win_target_2d": {"null_win_target": 25.0}}
MIN_RUNTIME_SQUEEZE = 0.75


def runtime_names(rows: list) -> dict[str, list[str]]:
    """Translated names of the RUNTIME_FIT tables (not their descriptions)."""
    tables = {t for _, _, t, _, _ in RUNTIME_FIT if isinstance(t, str)}
    ids = {i: t for _, _, t, _, _ in RUNTIME_FIT if isinstance(t, tuple) for i in t}
    names: dict = defaultdict(list)
    for r in rows:
        if r["kind"] != "bxon":
            continue
        if r["entry"] in tables and ("\n" in r["src"] or len(r["src"]) <= 16):
            names[r["entry"]].append(r["tr"])
        if r["sid"] in ids and r["tr"] not in names[ids[r["sid"]]]:
            names[ids[r["sid"]]].append(r["tr"])
    return names


def runtime_fit(lay: Bflyt, layout: str, names: dict[str, list[str]], fonts: dict,
                squeeze: bool = True) -> None:
    """Set lay.squeeze_names / lay.width_to for the RUNTIME_FIT panes of this layout.
    squeeze=False: widths only (ExportOptions.runtime_fit narrows each name in game instead)."""
    from .metrics import line_widths
    panes = {p.name: p for p in lay.text_panes()}
    for name, wanted, table, mode, width in RUNTIME_FIT:
        if name != layout:
            continue
        if table is None:
            lay.width_to.update({pn: width for pn in wanted})
            continue
        if not names.get(table):
            continue
        for pn in wanted:
            p = panes.get(pn)
            if p is None or not (0 <= p.font_index < len(lay.fonts)):
                continue
            font = fonts.get(lay.fonts[p.font_index].rsplit(".", 1)[0])
            if font is None or p.font_size[0] <= 0:
                continue
            real = ((width or p.width) - RUNTIME_RESERVE.get(layout, 0.0)) * p.scale[0] * font.width / p.font_size[0]
            texts = names[table]
            if mode == "line":
                widest = max(max(line_widths(t, font)) for t in texts)
            else:
                pre = "・" if mode == "bullet" else ""
                widest = max(line_widths(pre + t.replace(" \n", " ").replace("\n", " "), font)[0]
                             for t in texts)
            if widest > real and squeeze:
                lay.squeeze_names[pn] = max(MIN_RUNTIME_SQUEEZE, real / widest)
                if real / widest < MIN_RUNTIME_SQUEEZE:
                    lay.runtime_overflow[pn] = widest * MIN_RUNTIME_SQUEEZE - real
            if width:
                lay.width_to[pn] = width


# the message text panes of the win_talk_* layouts (not the sign board / speaker name panes)
TALK_PANES = ("txt", "txt_l", "txt1", "txt2", "txt_l1", "txt_l2")
# runtime_fit: layouts (prefixes) and panes whose one-line panes carry wrapping message text
# (battle lines "...takes 12 points{CR}of damage!" are one 18 px line high and wrap on purpose)
FIT_EXCLUDE_LAYOUTS = ("win_talk", "win_message", "dive_dngn_btlmsg")
FIT_EXCLUDE_PANES = ("txt_meesage1", "txt_meesage2")
MENU_PACKS = ("common_2d.pack", "common_3d.pack", "common_2way.pack")


def _patch_pack(original: bytes, rows: list, opts: ExportOptions, report: ExportReport,
                path: str = "", help_factor: float = 1.0, runtime=None) -> bytes | None:
    """help_factor > 1: widen the help window layouts (gamefont.HELP_LAYOUTS) in this pack."""
    pack = Pack.parse(original)
    help_members = {f"blyt/{n}.bflyt" for n in gamefont.HELP_LAYOUTS} if help_factor > 1 else set()
    by_entry: dict[str, list] = defaultdict(list)
    for r in rows:
        by_entry[r["entry"]].append(r)
    changed = False
    factor = opts.menu_buffer_factor if os.path.basename(path) in MENU_PACKS else opts.expand_text_buffers
    expand = factor > 1 or opts.min_text_buffer > 0
    for e in pack.entries:
        erows = by_entry.get(e.name, [])
        magic = e.data[:4]
        if magic == b"BXON" and erows:
            b = Bxon.parse(e.data)
            repl, row_of = {}, {}
            for r in erows:
                if r["kind"] == "bxon":
                    repl[r["loc"]] = r["tr"]
                    row_of[r["loc"]] = r
            if repl:
                e.data = b.rebuild(repl)
                e.data, pages = ietalk.split_pages(e.data)
                report.extra_pages = getattr(report, "extra_pages", 0) + pages
                for off, reason in b.skipped:
                    r = row_of[off]
                    report.skipped.append({"entry": r["id"], "text": r["src"][:60], "reason": reason})
                applied = len(repl) - len(b.skipped)
                report.applied += applied
                changed = changed or applied > 0
        elif magic == b"SARC" and (erows or expand or help_members or opts.talk_buffer or opts.talk_style
                                       or opts.talk_lines > 3 or opts.talk_wide_2d
                                       or (opts.map_labels and path.startswith("/ui/map/"))):
            sarc = Sarc.parse(e.data)
            members: dict[str, dict[int, str]] = defaultdict(dict)
            widen: dict[str, dict[int, float]] = defaultdict(dict)
            squeeze: dict[str, dict[int, float]] = defaultdict(dict)
            for r in erows:
                if r["kind"] == "bflyt":
                    members[r["member"]][r["loc"]] = encode_str(r["tr"])
                    if r["pane_scale"] > 1:
                        widen[r["member"]][r["loc"]] = r["pane_scale"]
                    if r["squeeze"] < 1:
                        squeeze[r["member"]][r["loc"]] = r["squeeze"]
            mrepl = {}
            for n in sarc.nodes:
                md = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                if md[:4] != b"FLYT":
                    continue
                texts = members.get(n.name, {})
                mapl = None
                if opts.map_labels and path.startswith("/ui/map/"):
                    from . import maplabel
                    mapl = maplabel.plan(md)
                    texts = {**texts, **mapl[0]}
                    for jp in mapl[4]:
                        report.warnings.append({"entry": None, "text": f"{path}:{n.name}",
                                                "problem": f"map label not recognised: {jp!r}"})
                is_help = n.name in help_members
                base = os.path.basename(n.name).rsplit(".", 1)[0]
                grow = TALK_GROW.get(base) if opts.talk_lines > 3 else None
                rt = base in {x[0] for x in RUNTIME_FIT} and runtime is not None
                wide = (TALK_WIDE_2D.get(base) or TALK_LOWER_2D.get(base)
                        if opts.talk_wide_2d and path not in TALK_WIDE_SKIP_PACKS else None)
                is_talk = ((opts.talk_buffer > 0 or opts.talk_style or grow or wide) and base.startswith("win_talk"))
                fit = opts.runtime_fit and not base.startswith(FIT_EXCLUDE_LAYOUTS)
                geo = PANE_GEOMETRY.get(base)
                if not texts and not expand and not is_help and not is_talk and not rt and not fit and not geo:
                    continue
                lay = Bflyt(md)
                if geo:
                    lay.set_width.update(geo[0])
                    lay.move_x.update(geo[1])
                if mapl:
                    lay.set_float.update(mapl[1])
                    lay.set_byte.update(mapl[2])
                    lay.set_width.update(mapl[3])
                if fit:
                    lay.mark_fit = set(FIT_EXCLUDE_PANES)
                    lay.mark_multi = opts.runtime_fit_multi
                lay.squeeze = squeeze.get(n.name, {})
                if rt:
                    runtime_fit(lay, base, runtime[0], runtime[1], squeeze=not opts.runtime_fit)
                    lay.move_x.update(RUNTIME_MOVE_X.get(base, {}))
                if is_talk:
                    names = [p.name for p in lay.text_panes() if p.name in TALK_PANES]
                    if opts.talk_buffer:
                        lay.min_buf = {nm: opts.talk_buffer for nm in names}
                    if opts.talk_style:
                        lay.text_style = {nm: tuple(opts.talk_style) for nm in names}
                    elif grow and base in TALK_STYLE:
                        lay.text_style = {nm: TALK_STYLE[base] for nm in names}
                    if grow:
                        k = (opts.talk_lines - 3) * TALK_LINE_PX
                        lay.move = {nm: (dy * k, dh * k) for nm, (dy, dh) in grow.items()}
                    if wide and base in TALK_LOWER_2D:
                        k = TALK_LOWER_2D_SHOWN / TALK_LOWER_2D_WIDTH
                        for p in lay.text_panes():
                            if p.name in wide:
                                lay.set_width[p.name] = TALK_LOWER_2D_WIDTH
                                lay.set_float.setdefault(p.name, {})[0x44] = k
                                lay.move_x[p.name] = (TALK_LOWER_2D_SHOWN - p.width) / 2
                    elif wide:
                        lay.set_width.update(wide[0])
                        lay.move_x.update(wide[1])
                mfactor = factor
                if (opts.expand_only is not None and os.path.basename(path) in MENU_PACKS
                        and (os.path.basename(path), n.name) not in opts.expand_only):
                    mfactor = 1.0
                if opts.expand_panes is not None and os.path.basename(path) in MENU_PACKS:
                    lay.grow_only = opts.expand_panes
                wd = dict(widen.get(n.name, {}))
                if is_help:
                    wd.update(lay.widen_window(help_factor))
                nd = lay.rebuild(texts, buf_factor=mfactor if expand else 1.0,
                                 buf_min=max(opts.min_text_buffer if expand else 0, opts.name_buffer),
                                 widen=wd)
                report.applied += len(texts)
                if nd != md:
                    mrepl[n.name] = nd
            if mrepl:
                e.data = sarc.rebuild(mrepl)
                changed = True
        elif magic[:2] == b"\xfa\xfa" and erows and opts.include_script_labels:
            repl = {r["loc"]: encode_str(r["tr"]) for r in erows if r["kind"] == "cnut"}
            if repl:
                e.data = cnut.replace_strings(e.data, repl)
                report.applied += len(repl)
                changed = True
    if not changed:
        return None
    return pack.build()


def run_export(project: Project, out_dir: str, opts: ExportOptions, progress=None) -> ExportReport:
    progress = progress or (lambda *a: None)
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    romfs_repl, exefs_repl, report, code_out = collect_changes(project, opts, progress)
    tid = project.config.get("title_id", "0004000000199200")
    base = os.path.splitext(os.path.basename(project.rom_path))[0]

    if opts.make_3ds:
        out3ds = os.path.join(out_dir, f"{base}_EN.3ds")
        build_image(project.rom_path, out3ds, romfs_repl, exefs_repl, progress,
                    keep_update_partitions=opts.keep_update_partitions)
        report.outputs["3ds"] = out3ds
        if opts.verify:
            progress("Verifying image", 0, 1)
            probs = verify_image(out3ds)
            if probs:
                report.warnings.extend({"entry": None, "text": out3ds, "problem": p} for p in probs)
            progress("Verifying image", 1, 1)

    if opts.make_layeredfs:
        progress("Writing LayeredFS patch", 0, 1)
        lroot = os.path.join(out_dir, "layeredfs")
        if os.path.isdir(lroot):
            shutil.rmtree(lroot)
        tdir = os.path.join(lroot, "luma", "titles", tid)
        for path, data in romfs_repl.items():
            outp = os.path.join(tdir, "romfs", path.lstrip("/").replace("/", os.sep))
            os.makedirs(os.path.dirname(outp), exist_ok=True)
            with open(outp, "wb") as fp:
                fp.write(data)
        if code_out is not None:
            os.makedirs(tdir, exist_ok=True)
            with open(os.path.join(tdir, "code.bin"), "wb") as fp:
                fp.write(code_out)   # Luma expects the decompressed code
        report.outputs["layeredfs"] = lroot
        progress("Writing LayeredFS patch", 1, 1)

    if opts.make_cia and report.outputs.get("3ds"):
        from .ctr.cia import build_cia
        progress("Building CIA", 0, 1)
        outcia = os.path.join(out_dir, f"{base}_EN.cia")
        build_cia(report.outputs["3ds"], outcia, progress)
        report.outputs["cia"] = outcia
        progress("Building CIA", 1, 1)

    report.seconds = time.time() - t0
    return report
