"""Apply translations from a project to the game files and build output images."""
from __future__ import annotations

import os
import re
import shutil
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import blz, cnut, codestr, fontimport
from .bflyt import Bflyt
from .bxon import Bxon
from .ctr.build import build_image, verify_image
from .ctr.formats import open_image
from .darc import Sarc
from .fontpatch import patch_font
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
    font_fixes: bool = True              # narrow over-wide ASCII glyph advances in fonts
    make_3ds: bool = True
    make_layeredfs: bool = True
    make_cia: bool = False
    verify: bool = True
    keep_update_partitions: bool = True
    include_script_labels: bool = False  # Squirrel string literals are lookup keys, not on-screen text
    latin_keyboard: bool = False         # name entry: katakana page becomes A-Z a-z (off: keys rendered as black boxes)
    # The big menu packs (item menu etc.) load thousands of tiny text boxes at once; growing them
    # all x2 overflowed the game's layout heap (crash on opening the item menu). They keep the
    # original buffers unless a tested factor is set here.
    menu_buffer_factor: float = 1.0


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


def validate(source: str, translation: str) -> list[str]:
    problems = []
    st, tt = tags_of(source), tags_of(translation)
    missing = [t for t in st if t not in tt]
    extra = [t for t in tt if t not in st]
    if missing:
        problems.append("missing tags: " + " ".join(sorted(set(missing))))
    if extra:
        problems.append("unknown tags: " + " ".join(sorted(set(extra))))
    if translation.count("{") - translation.count("{{") * 2 != translation.count("}") - translation.count("}}") * 2:
        problems.append("unbalanced braces (use {{ and }} for literal braces)")
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
        "SELECT e.id, e.kind, e.file, e.entry, e.member, e.loc, e.capacity, s.text AS src,"
        " COALESCE(NULLIF(e.override, ''), s.translation) AS tr, s.status"
        " FROM entry e JOIN source s ON s.id = e.source_id"
        " WHERE (e.override IS NOT NULL AND e.override != '') OR"
        " (s.translation IS NOT NULL AND s.translation != '' AND s.status >= ?)", (min_status,)).fetchall()

    by_file: dict[str, list] = defaultdict(list)
    from .metrics import FontSet, fold_to_font
    game_font = FontSet.for_project(project).fonts["3D14"]
    folded = []
    for r in rows:
        r = dict(r)
        r["tr"] = fold_to_font(r["tr"], game_font)   # no accents/hearts in the game fonts
        folded.append(r)
    rows = folded
    for r in rows:
        probs = validate(r["src"], r["tr"])
        for p in probs:
            report.warnings.append({"entry": r["id"], "text": r["src"][:60], "problem": p})
        if UNRESOLVED_PC.search(r["tr"]):          # never write "<pc>" into the game
            report.skipped.append({"entry": r["id"], "text": r["src"][:60], "reason": "unresolved PC markup"})
            continue
        by_file[r["file"]].append(r)

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
        targets = sorted((set(by_file) - {"exefs:.code"}) | layout_packs)

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
            new = _patch_pack(original, by_file.get(path, []), opts, report, path)
            if new is not None:
                romfs_repl[path] = new
                report.files_changed.append(path)
        progress("Applying translations", len(targets), len(targets))

        for path, full in overrides.items():
            with open(full, "rb") as fp:
                romfs_repl[path] = fp.read()
            report.files_changed.append(path + " (override)")

        # fonts: narrow Latin glyphs from the donor font (project setting), then width fixes
        donor = fontimport.active_donor(project)
        if opts.font_fixes or donor:
            cache_dir = os.path.join(project.workdir, "cache")
            for path in files:
                if path.startswith("/font/") and path.endswith(".bffnt") and path not in overrides:
                    data = romfs.read_file(files[path])
                    name = path.rsplit("/", 1)[-1][:-6]
                    patched = fontimport.apply(name, data, donor, cache_dir)
                    imported = patched != data
                    if opts.font_fixes:
                        patched = patch_font(patched)
                    if patched != data:
                        romfs_repl[path] = patched
                        report.files_changed.append(path + (" (Latin font)" if imported else " (font fix)"))

        # executable strings
        code_rows = by_file.get("exefs:.code", [])
        exheader = ncch.exheader()
        compressed = bool(exheader[0x0D] & 1)
        code = ncch.exefs().read(".code")
        if compressed:
            code = blz.decompress(code)
        new_code = code
        if code_rows:
            repl = {}
            for r in code_rows:
                raw = encode_str(r["tr"])
                need = len(raw.encode("utf-16-le")) + 2
                if need > r["capacity"]:
                    report.skipped.append({"entry": r["id"], "text": r["src"][:60],
                                           "reason": f"too long for executable slot "
                                                     f"({need // 2 - 1} > {r['capacity'] // 2 - 1} chars)"})
                    continue
                repl[r["loc"]] = (raw, r["capacity"])
                report.applied += 1
            if repl:
                new_code = codestr.patch(new_code, repl)
        if opts.latin_keyboard:
            patched = latin_keyboard(new_code)
            if patched is None:
                report.warnings.append({"entry": None, "text": "exefs:.code",
                                        "problem": "name-entry keyboard table not found; keyboard left unchanged"})
            else:
                new_code = patched
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


# Name-entry keyboard of the Japanese executable: one UTF-16 table of 100-key pages
# (hiragana, katakana at the same positions, digits) starting at entry 4; U+2423 marks an
# empty key. The katakana page is entries 104..203 (ア at 109, ポ at 203).
KEYBOARD_PAGE = 100
KATAKANA_PAGE_START = 104
KEYBOARD_EMPTY = "␣"
KEYBOARD_SIGNATURE = "␣" * 9 + "あいうえおかきくけこ"
# full-width forms: the keyboard draws ASCII as black boxes (it only handles full-width
# characters); fontpatch gives these letters ASCII-like advances for names in text
LATIN_KEYS = ("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－．＇＆！？")


def latin_keyboard(code: bytes) -> bytes | None:
    """Replace the katakana page of the name-entry keyboard with Latin letters, in place
    (same table size, empty keys stay empty). Returns None if the table is not found."""
    sig = KEYBOARD_SIGNATURE.encode("utf-16-le")
    at = code.find(sig)
    if at < 0 or code.find(sig, at + 2) >= 0:
        return None                            # missing or ambiguous: do not guess
    lo = at + 2 * KATAKANA_PAGE_START
    hi = lo + 2 * KEYBOARD_PAGE
    page = code[lo:hi].decode("utf-16-le")
    if page[5:10] != "アイウエオ" or page[-4:] != "ピプペポ":
        return None
    keys = iter(LATIN_KEYS)
    out = []
    for ch in page:
        if ch == KEYBOARD_EMPTY:
            out.append(ch)
        else:
            out.append(next(keys, KEYBOARD_EMPTY))   # leftover katakana slots become empty keys
    new_page = "".join(out).encode("utf-16-le")
    b = bytearray(code)
    b[lo:hi] = new_page
    return bytes(b)


def project_meta(project: Project, key: str, default=None):
    import json
    r = project.db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


MENU_PACKS = ("common_2d.pack", "common_3d.pack", "common_2way.pack")


def _patch_pack(original: bytes, rows: list, opts: ExportOptions, report: ExportReport,
                path: str = "") -> bytes | None:
    pack = Pack.parse(original)
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
                for off, reason in b.skipped:
                    r = row_of[off]
                    report.skipped.append({"entry": r["id"], "text": r["src"][:60], "reason": reason})
                applied = len(repl) - len(b.skipped)
                report.applied += applied
                changed = changed or applied > 0
        elif magic == b"SARC" and (erows or expand):
            sarc = Sarc.parse(e.data)
            members: dict[str, dict[int, str]] = defaultdict(dict)
            for r in erows:
                if r["kind"] == "bflyt":
                    members[r["member"]][r["loc"]] = encode_str(r["tr"])
            mrepl = {}
            for n in sarc.nodes:
                md = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                if md[:4] != b"FLYT":
                    continue
                texts = members.get(n.name, {})
                if not texts and not expand:
                    continue
                lay = Bflyt(md)
                nd = lay.rebuild(texts, buf_factor=factor if expand else 1.0,
                                 buf_min=opts.min_text_buffer if expand else 0)
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
