"""Translation project: extraction of all game text into an SQLite database."""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
import time
from typing import Callable

from . import blz, cnut, codestr
from .bflyt import Bflyt
from .bxon import Bxon
from .categories import EXCLUDED_BXON_TYPES, bxon_category, bxon_context
from .ctr.formats import open_image
from .darc import Sarc
from .pack import Pack
from .textcodec import decode_str, has_japanese, strip_tags

Progress = Callable[[str, int, int], None]

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL UNIQUE,
    plain TEXT NOT NULL,
    has_jp INTEGER NOT NULL,
    chars INTEGER NOT NULL,
    translation TEXT,
    status INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated REAL
);
CREATE TABLE IF NOT EXISTS entry (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    entry TEXT,
    member TEXT,
    loc INTEGER NOT NULL,
    capacity INTEGER,
    category TEXT NOT NULL,
    context TEXT,
    label TEXT,
    seq INTEGER NOT NULL,
    override TEXT
);
CREATE INDEX IF NOT EXISTS entry_source ON entry(source_id);
CREATE INDEX IF NOT EXISTS entry_cat ON entry(category);
CREATE INDEX IF NOT EXISTS entry_file ON entry(file, entry, member, seq);
CREATE INDEX IF NOT EXISTS source_status ON source(status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

STATUS_NAMES = {0: "untranslated", 1: "draft", 2: "translated", 3: "reviewed"}

# UI layout placeholder detection: strings made of filler characters only
_FILLER = set("あいうえおアイウエオ0123456789０１２３４５６７８９ー－-+/／:：%％,.、。 　\n×xX*＊?？!！()（）[]［］GgＧ★☆■□●○◆◇・…~～ヰ")


def is_placeholder(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if all(ch in _FILLER for ch in t):
        return True
    return False


class Project:
    def __init__(self, workdir: str):
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.db_path = os.path.join(self.workdir, "project.db")
        self.cfg_path = os.path.join(self.workdir, "project.json")
        self._local = threading.local()
        self.config = self._load_config()
        with self.db() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(source)")}
            # window size set by hand (px of the string's font, lines); NULL = automatic
            for col in ("box_width", "box_lines"):
                if col not in cols:
                    c.execute(f"ALTER TABLE source ADD COLUMN {col} INTEGER")

    # ------------------------------------------------------------------ infra
    def db(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def _load_config(self) -> dict:
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path, encoding="utf-8") as fp:
                return json.load(fp)
        return {}

    def save_config(self):
        with open(self.cfg_path, "w", encoding="utf-8") as fp:
            json.dump(self.config, fp, indent=2)

    @property
    def rom_path(self) -> str | None:
        return self.config.get("rom_path")

    @property
    def extracted(self) -> bool:
        return bool(self.config.get("extracted"))

    def code_movable(self) -> set[int]:
        """Offsets of executable strings the exporter can move to free space when the
        translation is longer than the original slot (so they have no length limit)."""
        if getattr(self, "_code_movable", None) is None:
            from .ctr.formats import open_image
            movable = set()
            if self.rom_path and os.path.exists(self.rom_path):
                f, _ncsd, ncch = open_image(self.rom_path)
                try:
                    exheader = ncch.exheader()
                    code = ncch.exefs().read(".code")
                    if exheader[0x0D] & 1:
                        code = blz.decompress(code)
                finally:
                    f.close()
                mover = codestr.Mover(code, exheader)
                for r in self.db().execute("SELECT DISTINCT loc, capacity FROM entry WHERE kind='code'"):
                    if mover.why_not(r["loc"], r["capacity"]) is None:
                        movable.add(r["loc"])
            self._code_movable = movable
        return self._code_movable

    # ------------------------------------------------------------------ extraction
    def extract(self, rom_path: str, dump_files: bool = True, progress: Progress | None = None):
        progress = progress or (lambda *a: None)
        rom_path = os.path.abspath(rom_path)
        f, ncsd, ncch = open_image(rom_path)
        try:
            if not ncch.is_decrypted:
                raise ValueError("The game image is encrypted. Please provide a decrypted .3ds "
                                 "(e.g. dumped with GodMode9 'decrypted' or decrypted with Batch CIA 3DS Decryptor).")
            romfs = ncch.romfs()
            exefs = ncch.exefs()
            self.config.update({
                "rom_path": rom_path,
                "product_code": ncch.product_code,
                "title_id": f"{ncch.program_id:016X}",
                "rom_size": os.path.getsize(rom_path),
                "extracted": False,
            })
            self.save_config()

            exheader = ncch.exheader()
            code = exefs.read(".code")
            if exheader[0x0D] & 1:
                code = blz.decompress(code)
            text_size = struct.unpack_from("<I", exheader, 0x18)[0]

            if dump_files:
                self._dump(romfs, exefs, code, progress)

            db = self.db()
            db.execute("DELETE FROM entry")
            # keep existing translations: sources are matched by text
            existing = {r["text"]: r["id"] for r in db.execute("SELECT id, text FROM source")}
            src_ids = dict(existing)

            def source_id(text: str) -> int:
                sid = src_ids.get(text)
                if sid is None:
                    plain = strip_tags(text)
                    cur = db.execute(
                        "INSERT INTO source(text, plain, has_jp, chars) VALUES (?,?,?,?)",
                        (text, plain, int(has_japanese(text)), len(plain)))
                    sid = cur.lastrowid
                    src_ids[text] = sid
                return sid

            rows = []

            def add(text, kind, file, entry, member, loc, capacity, category, context, label):
                rows.append((source_id(text), kind, file, entry, member, loc, capacity,
                             category, context, label, len(rows)))

            files = [x for x in romfs.files if x.path.endswith(".pack")]
            total = len(files)
            self._layout_packs = set()
            for i, rf in enumerate(files):
                if i % 50 == 0:
                    progress("Scanning text", i, total)
                if rf.path.startswith(("/3d/", "/2d/", "/effect", "/sound/", "/movie/")):
                    continue
                try:
                    pack = Pack.parse(romfs.read_file(rf))
                except Exception:
                    continue
                for e in pack.entries:
                    self._scan_entry(rf.path, e, add)
            progress("Scanning text", total, total)
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('layout_packs', ?)",
                       (json.dumps(sorted(self._layout_packs)),))

            # hardcoded strings in the executable
            for cs in codestr.find_strings(code, text_size):
                cat = "Debug (not shown in game)" if codestr.is_debug(cs.raw) else "Hardcoded (Executable)"
                add(cs.text, "code", "exefs:.code", None, None, cs.offset, cs.capacity, cat,
                    f"executable @ {0x100000 + cs.offset:#x} (max {cs.capacity // 2 - 1} chars)", None)

            db.executemany(
                "INSERT INTO entry(source_id, kind, file, entry, member, loc, capacity, category, context, label, seq)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            # drop orphaned untranslated sources (from a previous extraction of another version)
            db.execute("DELETE FROM source WHERE id NOT IN (SELECT source_id FROM entry) AND translation IS NULL")
            db.commit()
            self.config["extracted"] = True
            self.config["extracted_at"] = time.time()
            self.save_config()
            progress("Done", 1, 1)
        finally:
            f.close()

    def _scan_entry(self, path: str, e, add):
        d = e.data
        magic = d[:4]
        if magic == b"BXON":
            b = Bxon.parse(d)
            if b.type_name in EXCLUDED_BXON_TYPES:
                return
            cat = bxon_category(b.type_name, path, e.name)
            ctx = bxon_context(b.type_name, path, e.name)
            for s in b.strings:
                t = s.text
                if not has_japanese(t):
                    continue
                add(t, "bxon", path, e.name, None, s.offset, None, cat, ctx, b.type_name)
        elif magic == b"SARC":
            sarc = Sarc.parse(d)
            for n in sarc.nodes:
                md = sarc.raw[sarc.data_offset + n.start:sarc.data_offset + n.end]
                if md[:4] == b"FLYT":
                    self._layout_packs.add(path)
                    lay = Bflyt(md)
                    for p in lay.text_panes():
                        if not has_japanese(p.text):
                            continue
                        t = decode_str(p.text)
                        cat = "UI Layouts" if not is_placeholder(p.text) else "UI Placeholders"
                        add(t, "bflyt", path, e.name, n.name, p.index, None, cat,
                            f"{path.rsplit('/', 1)[-1]} · {n.name} · pane '{p.name}' "
                            f"({p.width:.0f}×{p.height:.0f}px)", p.name)
        elif magic[:2] == b"\xfa\xfa":
            for s in cnut.find_strings(d):
                if has_japanese(s.text):
                    add(s.text.replace("{", "{{").replace("}", "}}"), "cnut", path, e.name, None, s.offset, None,
                        "Script Labels (internal)", f"{path.rsplit('/', 1)[-1]} · {e.name}", None)

    def _dump(self, romfs, exefs, code: bytes, progress: Progress):
        base = os.path.join(self.workdir, "extracted")
        rdir = os.path.join(base, "romfs")
        edir = os.path.join(base, "exefs")
        os.makedirs(edir, exist_ok=True)
        for ent in exefs.entries:
            with open(os.path.join(edir, ent.name.lstrip(".") + ".bin"), "wb") as fp:
                fp.write(exefs.read(ent.name))
        with open(os.path.join(edir, "code.decompressed.bin"), "wb") as fp:
            fp.write(code)
        total = len(romfs.files)
        for i, rf in enumerate(romfs.files):
            if i % 200 == 0:
                progress("Extracting RomFS files", i, total)
            out = os.path.join(rdir, rf.path.lstrip("/").replace("/", os.sep))
            if os.path.exists(out) and os.path.getsize(out) == rf.size:
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fp:
                remaining, pos = rf.size, 0
                while remaining:
                    n = min(remaining, 8 << 20)
                    fp.write(romfs.read_file(rf, pos, n))
                    pos += n
                    remaining -= n
        progress("Extracting RomFS files", total, total)
        os.makedirs(os.path.join(self.workdir, "override", "romfs"), exist_ok=True)
