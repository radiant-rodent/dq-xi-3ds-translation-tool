"""Apply translations from a project to the game files and build output images."""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import blz, cnut, codefix, codestr, fontimport, gamefont, images
from .bflyt import Bflyt, numeric_text
from . import bubble, ietalk
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
    # Multiply UI text box buffers (1 = off: bflyt._rebuild_txt1 already grows static labels to fit
    # their English text, and 0x3A7A2C dynamically reallocates runtime text panes on demand; x2
    # wasted up to 1 MB of UI layout heap in non-menu packs like dive_dugeon_2way / battle / equip)
    expand_text_buffers: float = 1.0
    # Minimum UI text buffer in bytes (0 = off). Almost every text box is below 64 bytes, so a
    # minimum grows all of them and overflows the game's layout heap (crash when entering the
    # first field area).
    min_text_buffer: int = 0
    # Minimum buffer (bytes) of the text panes that are not numbers (0 = off: sstub in
    # grammar_asm.py adds +14 units at runtime whenever 0x3A7A2C sees a {v:N} tag or party name
    # code; setting 22 on every pane grew 1,103 panes in common_2way.pack by 1.58 MB of vertex
    # buffers and overflowed the UI heap when buying items in the Cobblestone shop)
    name_buffer: int = 0
    font_fixes: bool = True              # narrow over-wide ASCII glyph advances in fonts
    make_3ds: bool = True
    make_layeredfs: bool = True
    make_cia: bool = False
    make_patch: bool = False            # .dq11patch for the browser patcher (patchfile.py, docs/)
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
    # labels at most 1 px per letter too wide: character spacing -1 instead of narrower glyphs
    # (fractional x scales resample the glyphs: blurry on the 3DS screen)
    crisp_labels: bool = True
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
    # ... with the PC version's official English artwork where it has one (pctex.py, read from the
    # Steam install: the PC import's path or the default one; skipped when it is missing)
    pc_textures: bool = True
    # hand-edited textures: <workspace>/texture_edits/<texture>.png (same size as the 3DS one,
    # e.g. coinget_font_3d.png) and tutorial_XXXX.png for the tutorial pictures; used as they are
    texture_edits: bool = True
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
    bubble_font = FontSet.for_project(project).fonts[bubble.FONT]
    bubble_ids = bubble.entry_ids(project)
    folded = []
    for r in rows:
        r = dict(r)
        r["tr"] = fold_to_font(r["tr"], game_font)   # no accents/hearts in the game fonts
        if r["id"] in bubble_ids:
            r["tr"] = bubble.export_text(r["tr"], bubble_font)   # NPC speech bubble: 2 lines of 130 px
        elif r["kind"] == "bxon":
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
                gaps = max(len(l) for l in r["tr"].split("\n")) - 1
                if opts.crisp_labels and w > real and w - real <= gaps:
                    r["crisp"] = True     # 1 px less spacing per letter: no blurry resampled glyphs
                elif w > real:
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
        name_codes = namefix.assign([(r["src"], r["tr"]) for r in pi])
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

        # which packs need a rebuild: translated ones + all layout packs (buffer expansion / runtime_fit)
        layout_packs = set()
        if opts.expand_text_buffers > 1 or opts.min_text_buffer or opts.runtime_fit:
            layout_packs = set(project_meta(project, "layout_packs", []))
        help_packs = {p for p in files if os.path.basename(p) in MENU_PACKS} if help_factor > 1 else set()
        targets = sorted((set(by_file) - {"exefs:.code"}) | layout_packs | help_packs)
        runtime = (runtime_names(rows), gf._fonts(), digit_gaps(project))
        talk_level = 0
        if opts.runtime_fit and opts.talk_wide_2d:
            talk_level = lower_talk_level(project, rows, report)

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
                              help_factor if path in help_packs else 1.0, runtime, talk_level)
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
        keep = shown = None
        for path in files:
            if path.startswith("/font/") and path.endswith(".bffnt") and path not in overrides:
                data = romfs.read_file(files[path])
                name = path.rsplit("/", 1)[-1][:-6]
                patched = fontimport.build(name, data, opts.font_fixes, cache_dir)
                if opts.runtime_fit:
                    # narrow Latin glyphs: what the runtime fit draws too-wide text with
                    from . import fontnarrow
                    if keep is None:
                        keep, shown = _font_chars(db, rows)
                    patched = fontnarrow.build(name, patched, keep, shown, cache_dir)
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
            sfont = romfs_repl.get(f"/font/{texlabel.SMALL_FONT}.bffnt") or romfs.read_file(files[f"/font/{texlabel.SMALL_FONT}.bffnt"])
            official, edits, tutorial_edits = _texture_sources(project, opts, report)
            for path in files:
                if not (path.startswith("/ui/") and path.endswith(".pack")) or path in overrides:
                    continue
                if path.rsplit("/", 1)[-1] not in texlabel.PACKS:
                    continue
                data = romfs_repl.get(path) or romfs.read_file(files[path])
                new = texlabel.patch_pack(data, tfont, sfont, official=official, edits=edits)
                if new is not None:
                    romfs_repl[path] = new
                    if path not in report.files_changed:
                        report.files_changed.append(path + " (images)")
            if tutorial_edits:
                from . import bchtex
                for path in files:
                    stem = path.rsplit("/", 1)[-1][:-4]
                    if path.startswith("/ui/tutorial/") and stem in tutorial_edits:
                        data = romfs.read_file(files[path])
                        romfs_repl[path] = bchtex.replace(data, bchtex.textures(data)[0], tutorial_edits[stem])
                        report.files_changed.append(path + " (edited picture)")
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
    for path in namekbd.KEYBOARD_PACKS:
        if path not in files:
            continue
        new = namekbd.patch_pack(romfs_repl.get(path) or romfs.read_file(files[path]), font, accents)
        if new is not None:
            romfs_repl[path] = new
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
PROFILE_LABEL_MAX = 90.0     # px: label panes of the *_lw_dive profile layouts (80 px wide)
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
# Other pane geometry (layout -> ({pane: width}, {pane: dx}, optional {pane: (dy, dh)}, optional {pane: (font_size_y, line_space)},
# optional {pane: squeeze}, optional {pane: {section offset: float}}, optional {pane: {section offset: byte}})).
# Skill Panel (bottom screen): the description box was 120 px from x 15 (reflow_descriptions);
# the plate's frame starts at ~150, the left column ends at ~6. Raised by 3 px vertically with
# font_size_y=13.5 and line_space=0.5 so 4-line descriptions fit comfortably without touching the bottom frame.
PANE_GEOMETRY = {
    # Item descriptions: the 116 px text pane (3D) and the 106 px pane of the 15 px 2D font are
    # squeezed to 0.75 and 0.6, which gives both the same room (gamefont.ITEM_DESC_PANES)
    # (with the runtime fit the squeeze is off: narrow glyphs instead, Bflyt.narrow_fit).
    # "Can't buy" / "Can't sell" (3D): the right-aligned price panes were clamped to 42 / 38 px
    # and "Can't sell" was scaled past the narrowest glyphs (blurry, user 2026-10-22); 50 px
    # reach to just after the "Buy" / "Sell" labels
    "menu_dougu_details_2d": ({}, {}, None, None, {"txt_text": 0.6}),
    "item_details_3d": ({"txt_buy_num": 50.0, "txt_sale_num": 50.0}, {}, None, None, {"txt_text": 0.75}),
    "menu_dougu_details_3d": ({"txt_buy_num": 50.0, "txt_sale_num": 50.0}, {}, None, None, {"txt_text": 0.75}),
    # 2D battle Y hint ("Check status effects" / "Check planned actions"): 90 px from x -32,
    # nothing to its right on the bottom screen
    "battle_2d_lw": ({"txt_state": 150.0}, {}),
    # battle status box (lower screen): the centred 80 px name pane is as wide as the window
    # itself, so the runtime fit let "Hooded Stranger" run over both sides of the frame (the
    # Japanese placeholder passes it too, window_clamps leaves it); 70 px = inside the frame
    "win_hpmp_3d": ({"txt_name": 70.0}, {}),
    "win_hpmp_2d": ({"txt_name": 70.0}, {}),
    # 2D party list: the 70 px names from x 21 reached under the "HP" label at x 85
    "win_party_2d": ({f"txt_name{i}": 62.0 for i in range(1, 5)}, {}),
    # 2D Stats: the centred 150 px title ("Boy of Cobblestone") reached over the 148 px
    # window's frame; 138 px stay inside it
    "win_tsuyosa1_2d": ({"txt_title": 138.0}, {}),
    # accolade banner (3D): the title pane was drawn at scale 0.95 (blurry) and 230 px wide,
    # over the ornaments of the 280 px frame ("The Guidance of Yggdrasil", user 2026-10-22);
    # 1:1 and as wide as the dark inner plate, the runtime fit narrows longer titles
    "menu_senreki_title_3d": ({"txt_title": 160.0}, {}, None, None, None, {"txt_title": {0x44: 1.0, 0x48: 1.0}}),
    # Item List "32/41 types": three right-aligned panes placed for "999 / 999種"; the longer
    # "  41 types" ran under the slash and the count ("327A1", user 2026-10-22). The count and
    # slash move left, the types pane starts after the slash (left-aligned: its two leading
    # spaces are the gap), no negative spacing
    "common_3d_up_senreki": ({"txt_list4_r3": 72.0}, {"txt_list4_r1": -15.0, "txt_list4_r2": -17.0, "txt_list4_r3": -72.0},
                             None, None, None, {"txt_list4_r1": {0x78: 0.0}, "txt_list4_r2": {0x78: 0.0}},
                             {"txt_list4_r3": {0x09: 1, 0x5C: 5}}),
    # load screen bubble: the text sat in a 100 px corner box of the 128 px bubble; as wide as
    # the bubble's body and centred
    "win_load_story_3d": ({"txt_story1": 112.0}, {"txt_story1": -9.0}, None, None, None, None,
                          {"txt_story1": {0x5C: 4}}),
    "common_3d_lw_skill": (
        {"txt_pane": 128.0},
        {"txt_pane": -3.0},
        {"txt_pane": (3.0, 0.0)},
        {"txt_pane": (13.5, 0.5)},
    ),
    # Skill Panel tree totals ("Swordmastery: 0/ 7"): the right-aligned 96 px labels began 3 px
    # left of the window (x -152) and on its frame; 85 px keeps the right edge
    "menu_skill_get_3d": ({f"txt_{c}{k}": 85.0 for c in "lr" for k in (1, 2, 3)}, {}),
    # Forge weapon list: 172 px window had only 70 px columns with 79 px between them, so
    # "Greatswords" (x 98) ran over the right margin (x 165); widened to 216 px with Col 2
    # shifted right by 24 px
    "menu_senreki_list_buki_3d": (
        {"win": 216.0, **{f"txt_c{k}": 86.0 for k in range(1, 6)}, **{f"txt_l{k}": 82.0 for k in range(1, 7)}},
        {**{f"txt_c{k}": 24.0 for k in range(1, 6)}, **{f"null_txt_c{k}": 24.0 for k in range(1, 6)}},
    ),
    # Forge armour list: 140 px window had only 49 px for Col 2 ("Greatshields" overflowed);
    # widened to 180 px with Col 2 shifted right by 20 px
    "menu_senreki_list_bougu_3d": (
        {"win": 180.0, **{f"txt_c{k}": 65.0 for k in range(1, 4)}, **{f"txt_l{k}": 68.0 for k in range(1, 6)}},
        {**{f"txt_c{k}": 20.0 for k in range(1, 4)}, **{f"null_txt_c{k}": 20.0 for k in range(1, 4)}},
    ),
    # Item menu "Organise Items" button: the 96 px title centred at x 57 of the 100 px window
    # began at x 9, under the X icon (x 10-26). Windows widened 15 px to the left (25 covered
    # the corner of the item list frame); the title sits right of the icon
    "win_dougu_sort_3d": (
        {"win": 115.0, "line": 115.0, "txt_title": 85.0},
        {"null_win_sort": -15.0, "txt_title": 13.5, "txt": 7.5},
    ),
    "win_dougu_sort2_3d": (                       # the one-line variant shown in the item list
        {"win": 115.0, "txt_title": 89.0},
        {"null_win_sort2": -15.0, "txt_title": 10.5},
    ),
    "win_dougu_sort3_3d": (
        {"win": 115.0, "line": 115.0, "txt_title": 85.0},
        {"null_win_sort3": -15.0, "txt_title": 19.5, "txt": 7.5, "icon_key_r": 15.0},
    ),
    # field prompt ("[Y]:Action", "[A]:Help"): the A/Y icon covers x 9-25 and the text began
    # at x 23 / 25, where the Japanese full-width colon was mostly blank; ":Action" ran under
    # the icon (eggsoskeleton in Kingsbarrow, issue #3). The text starts 3-4 px right of the
    # icon; the 2D window (right edge at screen x 316) grows 8 px to the left for it
    "win_operation_3d": ({"txt_l1": 50.0}, {"txt_l1": 5.0}),
    "win_operation_2d": ({"win": 86.0}, {"null_win_operation": -8.0, "txt_l1": 4.0}),
    # 2D Pep Power description (right of the power list): 81x50 px of 2D9 held 3 lines, so the
    # runtime fit scaled the English to ~0.7 (blurry, issue #4). The window grows 20 px to the
    # right (the screen edge is 28 px away) and 16 px in height (8 up into the gap under the
    # battle scene, 8 down above the status windows): 101x66 px, six lines at line space 0,
    # which most descriptions fill with narrow glyphs only
    "win_renkei3_2d": ({"win": 120.0, "txt_l1": 101.0}, {},
                       {"null_win_renkei3": (8.0, 0.0), "win": (0.0, 16.0), "txt_l1": (0.0, 16.0)},
                       {"txt_l1": (11.0, 0.0)}),
}
# Text boxes the runtime fit scales although their Japanese placeholder overflows them
# (Bflyt.force_multi). 2D Pep Power description: 4 lines of placeholder in a 3-line 81x50 pane,
# so box_holds never marked it and the descriptions ran out of the window (issue #4)
FIT_MULTI_PANES = {"win_renkei3_2d": {"txt_l1"}}


# descriptions only: the field menu's message window does not wrap at runtime (a reflowed
# item message ran off its right edge, 2026-10-15)
# item.bxon: item descriptions, shown only in the item detail boxes (2D 106 px, 3D 116 px),
# which the runtime fit narrows level by level; pre-wrapped for one width, a line too long for
# the other box broke again and the box was scaled (blurry 2D shop text, user 2026-10-22)
REFLOW_FILES = ("battle_act_text.bxon", "passive_skill.bxon", "item.bxon")


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
            and (r["tr"].rstrip().endswith((".", "!", "?")) or len(r["tr"].split()) >= 6)
            and not r["tr"].startswith(("First Trial", "Second Trial", "Third Trial", "Fourth Trial", "Final Trial")))


# The game scrolls the text up once the lines no longer fit (font height + line space) into
# 66 px, but draws the lines with a fixed pitch: a line space of 0.5 lets 4 lines count as
# fitting without changing how they look
TALK_STYLE = {"win_talk_3d": (15.0, 2.0)}   # experimental (talk_lines=4 is off by default)
# Panes the game fills at runtime with names from a table (they have no text of their own to
# fit): (layout, panes, table, how the name is shown, new pane width or None). The glyphs of
# the pane are narrowed so the longest translated name fits ("line": every line of a two-line
# name, "joined": the name on one line, "bullet": "・" + the name on one line).
TARGET_COLUMN = 78.0
RUNTIME_FIT = [
    # the declared action (command / target) on the lower screen, one column per party member
    # under its status window (80 px apart, centred at x 40, 120, 200, 280): at most 78 px, or
    # the labels of neighbouring members overlap (user 2026-10-25, were 130 / 120 px). The
    # runtime fit (autofit "fim") narrows a long name until it fits the two lines
    ("win_target_3d", ("txt_target",), "enemy_base.bxon", "line", TARGET_COLUMN),
    ("win_target_3d", ("txt_command",), None, "line", TARGET_COLUMN),  # skill names: width only
    ("win_target_2d", ("txt_target",), "enemy_base.bxon", "line", TARGET_COLUMN),
    ("win_target_2d", ("txt_command",), None, "line", TARGET_COLUMN),
    # enemy list: the count column right of the names is one digit now (codefix "%d匹"), so the
    # names may use the room up to it (window 240 / 225 px, names start at x 25 / 21; the digit
    # (6 / 8 px) ends at x 226 / 214). The pane must hold the longest name at the narrowest glyph
    # level / autofit.MARGIN, or the fit scales it (blurry "Vicious hard-boiled eggsoskeleton",
    # 2D9 185 px; user 2026-10-25): wider, and moved left (RUNTIME_MOVE_X). 3D: 199 px, the
    # cursor is right before the names; only that name (3D12 198 px) still gets a 1.5 % scale
    ("win_enemy_3d", tuple(f"txt_l{i}" for i in range(1, 8)), "enemy_base.bxon", "joined", 199.0),
    ("win_enemy_2d", tuple(f"txt_l{i}" for i in range(1, 8)), "enemy_base.bxon", "joined", 189.0),
    ("item_details_3d", ("txt_monster",), "enemy_base.bxon", "bullet", None),
    ("item_details_2d", ("txt_monster",), "enemy_base.bxon", "bullet", None),
    # battle commands, right column (Abilities / Items / Equip, filled at runtime): 3D panes
    # widened from 50 to 54 px (right-aligned at x 144, still clear of the left column)
    ("win_command_3d", ("txt_r1", "txt_r2", "txt_r3"), (8049, 7633, 11540), "line", 54.0),
    # 2D: 44 px, the narrowest 2D9 "Abilities" is 43 px (its i/l/t hardly condense); a pane
    # narrower than that scaled it past the last glyph level: blurry (user 2026-10-25)
    ("win_command_2d", ("txt_r1", "txt_r2", "txt_r3"), (8049, 7633, 11540), "line", 44.0),
    # the "Organise Items" button of the bag (mainmenu_text 41442); the X icon takes ~18 px
    ("win_dougu_sort2_3d", ("txt_title",), (41442,), "joined", None),
    ("win_dougu_sort2_2d", ("txt_title",), (41442,), "joined", None),
]
RUNTIME_RESERVE = {"win_dougu_sort2_3d": 18.0, "win_dougu_sort2_2d": 18.0}   # px not usable by text
# Pane moves: (the target labels were moved 25 px right for their 130 / 120 px panes; that
# pushed the 4th member's label off the screen's right edge, 2026-10-25)
RUNTIME_MOVE_X: dict = {
    # 2D battle commands (window 125 px): the left words (panes right-anchored at x 81, text from
    # 21) ran into the right ones ("AttackAbilities", "DefendEquip", user 2026-10-25). Left
    # words now in 21..60 (crisp glyph level 3 at most: "Defend" 37 px), before the right
    # column's cursor (62..78); right words in 76..120
    "win_command_2d": {"txt_l1": -21.0, "txt_l2": -21.0, "txt_l3": -21.0,
                       "txt_r1": 2.0, "txt_r2": 2.0, "txt_r3": 2.0},
    # enemy list names start closer to the cursor (2D x 19, 3D x 24): the longest name ends
    # before the count digit
    "win_enemy_2d": {f"txt_l{i}": -2.0 for i in range(1, 8)},
    "win_enemy_3d": {f"txt_l{i}": -1.0 for i in range(1, 8)}}
# The target labels three lines high instead of two (2D 30 -> 36 px, 2D9 11 px lines; 3D 40 ->
# 48 px, 15 px lines), the top edge where it was: in the 78 px column 35 / 68 monster names
# needed a third line, which the runtime fit could only get by scaling the text (blurry, user
# 2026-10-25); with three lines every name fits in crisp glyphs. pane -> (dy, dheight)
RUNTIME_MOVE = {"win_target_2d": {"txt_target": (-3.0, 6.0)},
                # 3D: the whole group 5 px up, or a third line sits on the panel's lower frame
                "win_target_3d": {"txt_target": (-4.0, 8.0), "null_win_target": (5.0, 0.0)}}
# panes set to a width narrower than their own (the runtime fit narrows the text to it)
RUNTIME_WIDTH_EXACT = {"win_command_2d": {"txt_l1": 39.0, "txt_l2": 39.0, "txt_l3": 39.0},
                       # the Japanese 3D action labels are 90 px, wider than their column
                       "win_target_3d": {"txt_target": TARGET_COLUMN, "txt_command": TARGET_COLUMN}}
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


def digit_gaps(project) -> dict[str, int]:
    """Font name -> the narrowest gap (px) between two digits of that font at character
    spacing 0: the right bearing of one digit plus the empty columns before the next."""
    from .fontimport import Font
    from .metrics import FontSet
    out = {}
    for name, data in FontSet.for_project(project).raw.items():
        f = Font(data)
        rights, lefts = [], []
        for c in "0123456789":
            gi = f.cmap.get(ord(c))
            if gi is None:
                continue
            l, gw, adv = f.width(gi)
            xs = [x for x in range(gw) if any(r[x] >= 6 for r in f.glyph(gi))]
            if xs:
                lefts.append(l + xs[0])
                rights.append(adv - (l + xs[-1] + 1))
        if rights:
            out[name] = min(rights) + min(lefts)
    return out


def number_spacing(lay: Bflyt, gaps: dict[str, int]) -> None:
    """Number panes keep the negative character spacing of the Japanese layout (-1..-3, set
    for the wide Japanese digits); the Latin digits then touched ("131", user 2026-10-22).
    Their spacing is raised so that two digits stay at least one pixel apart."""
    for p in lay.text_panes():
        if not (0 <= p.font_index < len(lay.fonts)) or not numeric_text(p.text):
            continue
        gap = gaps.get(lay.fonts[p.font_index].rsplit(".", 1)[0])
        cs = struct.unpack_from("<f", lay.data, p.sec_offset + 0x78)[0]
        # (2D14SH: < 0, its outline rings overlap on purpose)
        if gap is not None and gap >= 0 and cs < 0 and gap + cs < 1:
            lay.set_float.setdefault(p.name, {})[0x78] = float(min(0, 1 - gap))


def halfwidth_labels(lay: Bflyt) -> dict[int, str]:
    """Pane texts made of full-width Latin letters, digits and punctuation only ("ＨＰ", "ＭＰ",
    "ＨＰ：" - not extracted as Japanese), written in ASCII. The fonts draw a full-width letter
    with a space in front (fontimport: monster suffixes "Slime Ａ"), so "ＭＰ" ran over its
    30 px pane and wrapped into "M" / "P" (user 2026-10-22)."""
    out = {}
    for p in lay.text_panes():
        t = p.text
        if t and any(0xFF21 <= ord(c) <= 0xFF5A for c in t) and all(0xFF01 <= ord(c) <= 0xFF5E for c in t):
            out[p.index] = encode_str("".join(chr(ord(c) - 0xFEE0) for c in t))
    return out


def _font_chars(db, rows) -> tuple[set[int], set[int]]:
    """(every character of the Japanese text and the translations, the characters of the
    strings the exported game shows) for fontnarrow.build."""
    keep: set[int] = set()
    for text, tr in db.execute("SELECT text, translation FROM source"):
        keep.update(map(ord, text))
        keep.update(map(ord, tr or ""))
    shown: set[int] = set()
    done = {r["sid"] for r in rows}
    for r in rows:
        shown.update(map(ord, r["tr"]))
    for sid, text in db.execute("SELECT id, text FROM source"):
        if sid not in done:
            shown.update(map(ord, text))
    return keep, shown


def lower_talk_level(project, rows: list, report: ExportReport) -> int:
    """Narrow glyph level of the lower-screen 2D talk window (TALK_LOWER_2D): the lowest at
    which every dialogue line (wrapped for TALK_LOWER_2D_WIDTH) fits TALK_LOWER_2D_SHOWN px."""
    from . import fontnarrow
    from .metrics import FontSet, line_widths
    font_set = FontSet.for_project(project)
    data, font = font_set.raw["2D14"], font_set.fonts["2D14"]
    lines = [ln for r in rows if r["kind"] == "bxon" and r["category"] in gamefont.DIALOGUE_CATEGORIES
             for ln in r["tr"].replace("{PAGE}", "\n").split("\n")]
    level, over = fontnarrow.level_for(lines, lambda ln: line_widths(ln, font)[0],
                                       fontnarrow.savings(data), TALK_LOWER_2D_SHOWN)
    for ln in over[:20]:
        report.warnings.append({"entry": None, "text": ln[:60],
                                "problem": "too wide for the lower-screen 2D talk window"})
    return level


def _patch_pack(original: bytes, rows: list, opts: ExportOptions, report: ExportReport,
                path: str = "", help_factor: float = 1.0, runtime=None, talk_level: int = 0) -> bytes | None:
    """help_factor > 1: widen the help window layouts (gamefont.HELP_LAYOUTS) in this pack.
    talk_level: narrow glyph level of the lower-screen 2D talk window (0: x scale instead)."""
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
        elif magic == b"SARC" and (erows or expand or opts.runtime_fit or help_members or opts.talk_buffer or opts.talk_style
                                       or opts.talk_lines > 3 or opts.talk_wide_2d
                                       or (opts.map_labels and path.startswith("/ui/map/"))):
            sarc = Sarc.parse(e.data)
            members: dict[str, dict[int, str]] = defaultdict(dict)
            widen: dict[str, dict[int, float]] = defaultdict(dict)
            squeeze: dict[str, dict[int, float]] = defaultdict(dict)
            crisp: dict[str, set[int]] = defaultdict(set)
            for r in erows:
                if r["kind"] == "bflyt":
                    members[r["member"]][r["loc"]] = encode_str(r["tr"])
                    if r["pane_scale"] > 1:
                        widen[r["member"]][r["loc"]] = r["pane_scale"]
                    if r["squeeze"] < 1:
                        squeeze[r["member"]][r["loc"]] = r["squeeze"]
                    if r.get("crisp"):
                        crisp[r["member"]].add(r["loc"])
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
                    if len(geo) > 2 and geo[2]:
                        lay.move.update(geo[2])
                    if len(geo) > 3 and geo[3]:
                        lay.text_style.update(geo[3])
                    if len(geo) > 4 and geo[4]:
                        lay.squeeze_names.update(geo[4])
                    if len(geo) > 5 and geo[5]:
                        for pn, vals in geo[5].items():
                            lay.set_float.setdefault(pn, {}).update(vals)
                    if len(geo) > 6 and geo[6]:
                        for pn, vals in geo[6].items():
                            lay.set_byte.setdefault(pn, {}).update(vals)
                if mapl:
                    lay.set_float.update(mapl[1])
                    lay.set_byte.update(mapl[2])
                    lay.set_width.update(mapl[3])
                if fit:
                    lay.mark_fit = set(FIT_EXCLUDE_PANES)
                    lay.mark_multi = opts.runtime_fit_multi
                    lay.force_multi = FIT_MULTI_PANES.get(base, set())
                    lay.narrow_fit = True
                    if base.endswith("_lw_dive") and runtime is not None:
                        # StreetPass / Draconian profile rows (label left, right-aligned value):
                        # the label panes are long, their text is not, so a long value
                        # ("Slot Machines") only has to stay clear of the label's text
                        from .metrics import line_widths as _lw
                        f12 = runtime[1]["3D12"]
                        lay.label_max = PROFILE_LABEL_MAX
                        lay.text_px = lambda t, f=f12: _lw(t, f)[0] * 13.0 / f.width
                lay.squeeze = squeeze.get(n.name, {})
                lay.crisp = crisp.get(n.name, set())
                if runtime is not None and len(runtime) > 2:
                    number_spacing(lay, runtime[2])
                if rt:
                    runtime_fit(lay, base, runtime[0], runtime[1], squeeze=not opts.runtime_fit)
                    lay.move_x.update(RUNTIME_MOVE_X.get(base, {}))
                    lay.width_exact.update(RUNTIME_WIDTH_EXACT.get(base, {}))
                    lay.move.update(RUNTIME_MOVE.get(base, {}))
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
                            if p.name not in wide:
                                continue
                            lay.set_width[p.name] = TALK_LOWER_2D_WIDTH
                            if talk_level:
                                # full-size narrow glyphs (autofit "fl<level>") instead of a
                                # 0.8 x scale, which drew the text blurred; the pane is as
                                # wide as the top window's, so the lines break the same way.
                                # Left-aligned text keeps its left edge, centred text its centre
                                left = lay.data[p.sec_offset + 0x5C] & 3 == 1
                                lay.set_byte[p.name] = dict(zip(range(0x24, 0x27), b"fl" + str(talk_level).encode()))
                                lay.move_x[p.name] = ((TALK_LOWER_2D_WIDTH if left else TALK_LOWER_2D_SHOWN)
                                                      - p.width) / 2
                            else:
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
                texts = {**halfwidth_labels(lay), **texts}
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


def _texture_sources(project: Project, opts: ExportOptions, report: ExportReport):
    """(official PC artwork or None, {texture: hand-edited RGBA}, {tutorial: hand-edited RGBA})."""
    import numpy as np
    from PIL import Image
    official = None
    if opts.pc_textures:
        from . import pcref, pctex
        try:
            row = project.db().execute("SELECT value FROM meta WHERE key='pc_path'").fetchone()
        except Exception:
            row = None
        root = (json.loads(row[0]) if row and row[0] else None) or pcref.DEFAULT_PC_PATH    # stored as JSON
        try:
            official = pctex.official(pctex.PcTextures(root))
        except (OSError, KeyError, ValueError) as e:
            report.warnings.append({"entry": None, "text": "PC artwork",
                                    "problem": f"PC version not readable ({e}); casino and race signs drawn instead"})
    edits, tutorials = {}, {}
    folder = os.path.join(project.workdir, "texture_edits")
    if opts.texture_edits and os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith(".png"):
                continue
            rgba = np.asarray(Image.open(os.path.join(folder, f)).convert("RGBA"))
            stem = f[:-4]
            if stem.startswith("tutorial_"):
                tutorials[stem] = rgba
            else:
                edits[f"timg/{stem}.bflim"] = rgba
    return official, edits, tutorials


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
            from . import luma       # Luma patches code.bin as it loads it: nothing of ours may be there
            report.warnings.extend({"entry": None, "text": "layeredfs code.bin", "problem": p}
                                   for p in luma.check_rom(code_out, project.rom_path))
        report.outputs["layeredfs"] = lroot
        progress("Writing LayeredFS patch", 1, 1)

    if opts.make_patch:
        from .patchfile import write_patch
        outp = os.path.join(out_dir, f"{base}_EN.dq11patch")
        write_patch(project.rom_path, outp, romfs_repl, exefs_repl, "Dragon Quest XI (3DS) English",
                    time.strftime("%Y-%m-%d"), opts.keep_update_partitions, progress)
        report.outputs["patch"] = outp

    if opts.make_cia and report.outputs.get("3ds"):
        from .ctr.cia import build_cia
        progress("Building CIA", 0, 1)
        outcia = os.path.join(out_dir, f"{base}_EN.cia")
        build_cia(report.outputs["3ds"], outcia, progress)
        report.outputs["cia"] = outcia
        progress("Building CIA", 1, 1)

    report.seconds = time.time() - t0
    return report
