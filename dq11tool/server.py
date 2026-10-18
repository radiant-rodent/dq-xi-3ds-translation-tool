"""Local web UI for the DQXI 3DS translation toolkit (Flask)."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import traceback

from flask import Flask, Response, jsonify, request, send_from_directory

from . import gamefont, pcref
from .categories import CATEGORY_ORDER
from .export import ExportOptions, run_export, validate
from .metrics import FontSet, font_meta, measure, sheet_png, wrap
from .project import STATUS_NAMES, Project
from .textcodec import encode_str, has_japanese

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
HIDDEN_CATEGORIES = {"UI Placeholders", "Debug (not shown in game)", "Script Labels (internal)"}
PREVIEW_FONTS = ("3D14", "3D12", "2D14", "2D9", "2D14SH", "credit2D")


class Jobs:
    """One background job at a time, with progress reporting."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False}
        self.cancel = threading.Event()

    def start(self, name: str, fn):
        """fn(progress, cancel_event) runs in a background thread."""
        with self.lock:
            if self.state.get("running"):
                raise RuntimeError("another job is still running")
            self.cancel = threading.Event()
            self.state = {"running": True, "name": name, "phase": "Starting", "done": 0, "total": 0,
                          "started": time.time(), "error": None, "result": None, "cancellable": name == "translate"}

        def progress(phase, done, total):
            self.state.update(phase=phase, done=done, total=total)

        cancel = self.cancel

        def run():
            try:
                res = fn(progress, cancel)
                self.state.update(result=res)
            except Exception as e:  # report to UI
                self.state.update(error=f"{e}", trace=traceback.format_exc())
            finally:
                self.state.update(running=False, finished=time.time())

        threading.Thread(target=run, daemon=True).start()


def create_app(workdir: str) -> Flask:
    app = Flask(__name__, static_folder=None)
    project = Project(workdir)
    if project.config.get("extracted"):
        pcref.ensure_schema(project)
    jobs = Jobs()

    def fonts() -> FontSet:
        return FontSet.for_project(project)

    def main_font():
        return fonts().fonts["3D14"]

    def game_fonts() -> gamefont.GameFonts:
        return gamefont.for_project(project)

    problem_cache = {"key": None, "ids": set()}
    problem_lock = threading.Lock()

    def has_problem(sid: int, text: str, tr: str, gf, official: bool = False) -> bool:
        """Same checks as the editor warnings: window fit, placeholders / PC markup, font glyphs."""
        if not gf.fits(sid, text, tr) or validate(text, tr, official):
            return True
        font = gf.font(sid)
        return any(ord(c) >= 0x20 and ord(c) not in font.cmap for c in tr if c not in "\n")

    def problem_ids() -> set[int]:
        """Ids of translated strings with a problem; recomputed only when translations changed."""
        db = project.db()
        key = tuple(db.execute("SELECT COUNT(*), MAX(updated), SUM(LENGTH(translation)) FROM source"
                               " WHERE translation IS NOT NULL AND translation != ''").fetchone()) + (fonts().key,)
        with problem_lock:
            if problem_cache["key"] != key:
                gf = game_fonts()
                rows = db.execute("SELECT id, text, translation, origin FROM source"
                                  " WHERE translation IS NOT NULL AND translation != ''").fetchall()
                problem_cache["ids"] = {r[0] for r in rows if has_problem(r[0], r[1], r[2], gf, r[3] == "pc")}
                problem_cache["key"] = key
            return set(problem_cache["ids"])

    # ------------------------------------------------------------------ static
    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(WEB, name)

    # ------------------------------------------------------------------ project
    @app.get("/api/state")
    def state():
        return jsonify({"workdir": project.workdir, "config": project.config,
                        "extracted": project.extracted, "job": jobs.state})

    @app.get("/api/job")
    def job():
        return jsonify(jobs.state)

    @app.post("/api/browse")
    def browse():
        kind = (request.json or {}).get("kind", "rom")
        result = {}

        def ask():
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "dir":
                result["path"] = filedialog.askdirectory(parent=root, title="Choose a folder")
            elif kind == "pak":
                result["path"] = filedialog.askopenfilename(
                    parent=root, title="Choose the Japanese text .pak",
                    filetypes=[("Unreal pak", "*.pak"), ("All files", "*.*")])
            else:
                result["path"] = filedialog.askopenfilename(
                    parent=root, title="Choose the decrypted Dragon Quest XI .3ds",
                    filetypes=[("3DS game image", "*.3ds *.cci"), ("All files", "*.*")])
            root.destroy()

        t = threading.Thread(target=ask)
        t.start()
        t.join()
        return jsonify({"path": result.get("path") or ""})

    @app.post("/api/extract")
    def extract():
        body = request.json or {}
        rom = body.get("rom_path", "").strip().strip('"')
        if not rom or not os.path.isfile(rom):
            return jsonify({"error": "File not found: " + rom}), 400
        dump = bool(body.get("dump_files", True))
        try:
            jobs.start("extract", lambda prog, cancel: project.extract(rom, dump_files=dump, progress=prog))
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 409
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ browsing strings
    # ------------------------------------------------------------------ PC reference script
    @app.get("/api/pc/status")
    def pc_status():
        pcref.ensure_schema(project)
        db = project.db()
        n = db.execute("SELECT COUNT(*) FROM pc_text").fetchone()[0]
        m = db.execute("SELECT SUM(kind='exact'), SUM(kind='fuzzy') FROM pc_match WHERE rank=0").fetchone()
        official = db.execute("SELECT COUNT(*) FROM source WHERE origin='pc'").fetchone()[0]
        with_jp = db.execute("SELECT COUNT(*) FROM pc_text WHERE jp != ''").fetchone()[0]
        return jsonify({"lines": n, "exact": m[0] or 0, "similar": m[1] or 0, "official": official,
                        "default_path": pcref.DEFAULT_PC_PATH, "imported_from": project_meta_get("pc_path"),
                        "jp_pak": project_meta_get("pc_jp_pak"), "with_jp": with_jp})

    @app.post("/api/pc/import")
    def pc_import():
        body = request.json or {}
        path = (body.get("path") or pcref.DEFAULT_PC_PATH).strip()
        jp_pak = (body.get("jp_pak") or "").strip()
        rematch_only = bool(body.get("rematch_only"))
        if not rematch_only and not os.path.isdir(path):
            return jsonify({"error": f"folder not found: {path}"}), 400
        if jp_pak and not os.path.isfile(jp_pak):
            return jsonify({"error": f"file not found: {jp_pak}"}), 400

        def run(prog, cancel):
            if not rematch_only:
                recs = pcref.extract_pc(path, prog, jp_paks=[jp_pak] if jp_pak else None)
                pcref.store_pc_text(project, recs)
                project_meta_set("pc_path", path)
                project_meta_set("pc_jp_pak", jp_pak)
            return pcref.match_all(project, pcref.convert_en, prog, cancel)
        try:
            jobs.start("pcimport", run)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 409
        return jsonify({"ok": True})

    def project_meta_get(key):
        r = project.db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    def project_meta_set(key, value):
        db = project.db()
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        db.commit()

    @app.get("/api/categories")
    def categories():
        db = project.db()
        rows = db.execute(
            "SELECT e.category AS name, COUNT(DISTINCT e.source_id) AS total,"
            " COUNT(DISTINCT CASE WHEN s.status >= 2 THEN e.source_id END) AS done,"
            " COUNT(DISTINCT CASE WHEN s.status = 1 THEN e.source_id END) AS draft"
            " FROM entry e JOIN source s ON s.id = e.source_id GROUP BY e.category").fetchall()
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        cats = [dict(r) | {"hidden": r["name"] in HIDDEN_CATEGORIES} for r in rows]
        cats.sort(key=lambda c: (c["hidden"], order.get(c["name"], 99), c["name"]))
        tot = db.execute(
            "SELECT COUNT(*) AS total, SUM(status >= 2) AS done, SUM(status = 1) AS draft FROM source"
            " WHERE id IN (SELECT source_id FROM entry WHERE category NOT IN (%s))"
            % ",".join("?" * len(HIDDEN_CATEGORIES)), tuple(HIDDEN_CATEGORIES)).fetchone()
        return jsonify({"categories": cats, "total": dict(tot)})

    @app.get("/api/files")
    def files():
        cat = request.args.get("category")
        db = project.db()
        if cat:
            rows = db.execute("SELECT file, COUNT(DISTINCT source_id) AS n FROM entry WHERE category=?"
                              " GROUP BY file ORDER BY file", (cat,)).fetchall()
        else:
            rows = db.execute("SELECT file, COUNT(DISTINCT source_id) AS n FROM entry GROUP BY file"
                              " ORDER BY file").fetchall()
        return jsonify([dict(r) for r in rows])

    def _filters():
        a = request.args
        where, params = [], []
        cat = a.get("category")
        file = a.get("file")
        sub = []
        if cat:
            sub.append("category = ?")
            params.append(cat)
        elif a.get("show_hidden") != "1":
            sub.append("category NOT IN (%s)" % ",".join("?" * len(HIDDEN_CATEGORIES)))
            params.extend(HIDDEN_CATEGORIES)
        if file:
            sub.append("file = ?")
            params.append(file)
        if sub:
            where.append("s.id IN (SELECT source_id FROM entry WHERE " + " AND ".join(sub) + ")")
        st = a.get("status", "all")
        if st == "untranslated":
            where.append("s.status = 0")
        elif st == "draft":
            where.append("s.status = 1")
        elif st == "translated":
            where.append("s.status = 2")
        elif st == "reviewed":
            where.append("s.status = 3")
        elif st == "todo":
            where.append("s.status < 2")
        elif st == "problems":
            ids = problem_ids()
            where.append("s.id IN (%s)" % (",".join(map(str, sorted(ids))) or "-1"))
        pc = a.get("pc")
        if pc == "official":
            where.append("s.origin = 'pc'")
        elif pc == "similar":
            where.append("s.id IN (SELECT source_id FROM pc_match WHERE rank = 0 AND kind = 'fuzzy')")
        elif pc == "none":
            where.append("s.id NOT IN (SELECT source_id FROM pc_match)")
        q = (a.get("q") or "").strip()
        if q:
            where.append("(s.text LIKE ? OR s.plain LIKE ? OR s.translation LIKE ? OR s.note LIKE ?)")
            params.extend([f"%{q}%"] * 4)
        return ("WHERE " + " AND ".join(where)) if where else "", params

    @app.get("/api/strings")
    def strings():
        a = request.args
        page = max(int(a.get("page", 1)), 1)
        per = min(max(int(a.get("per", 50)), 1), 500)
        where, params = _filters()
        db = project.db()
        total = db.execute(f"SELECT COUNT(*) FROM source s {where}", params).fetchone()[0]
        order = "first_entry"
        if a.get("sort") == "length":
            order = "s.chars DESC"
        elif a.get("sort") == "recent":
            order = "s.updated DESC"
        # each string is shown through its first occurrence inside the browsed context;
        # one grouped pass over entry instead of a correlated subquery per string (that took 47 s)
        ctx, ctx_params = [], []
        if a.get("category"):
            ctx.append("category = ?"); ctx_params.append(a["category"])
        if a.get("file"):
            ctx.append("file = ?"); ctx_params.append(a["file"])
        ctx_where = ("WHERE " + " AND ".join(ctx)) if ctx else ""
        rows = db.execute(
            f"SELECT s.*, x.first_entry FROM source s"
            f" JOIN (SELECT source_id, MIN(id) AS first_entry FROM entry {ctx_where} GROUP BY source_id) x"
            f" ON x.source_id = s.id {where} ORDER BY {order} LIMIT ? OFFSET ?",
            ctx_params + params + [per, (page - 1) * per]).fetchall()
        items = []
        font = main_font()
        for r in rows:
            r = dict(r)
            r["occurrences"] = db.execute("SELECT COUNT(*) FROM entry WHERE source_id=?", (r["id"],)).fetchone()[0]
            e = db.execute("SELECT kind, file, entry, category, context, capacity, loc FROM entry WHERE id=?",
                           (r["first_entry"],)).fetchone()
            items.append(_item(r, e, font))
        return jsonify({"total": total, "page": page, "per": per, "items": items})

    def _item(r, e, _font=None):
        gf = game_fonts()
        font = gf.font(r["id"])                 # measured in the font the game draws it with
        src_m = measure(r["text"], font)
        d = {k: r[k] for k in ("id", "text", "translation", "status", "note", "chars", "occurrences", "origin")
             if k in r.keys()}
        m = project.db().execute("SELECT kind, score FROM pc_match WHERE source_id=? AND rank=0", (r["id"],)).fetchone()
        if m:
            d.update(pc_kind=m["kind"], pc_score=round(m["score"], 2))
        box = gf.get(r["id"], r["text"])
        # executable strings the exporter can move to free space have no length limit
        cap = 0 if e["capacity"] and e["loc"] in project.code_movable() else e["capacity"]
        d.update(category=e["category"], context=e["context"], kind=e["kind"], file=e["file"],
                 capacity=cap, src_width=src_m["max_width"], src_lines=src_m["lines_per_page"],
                 box_width=box["width"], box_lines=box["lines"], max_chars=box["max_chars"],
                 box_auto_width=box["auto_width"], box_auto_lines=box["auto_lines"],
                 help_window=box["help_window"], help_max=box["help_max"])
        if gf.source_font(r["id"]) == gamefont.MESSAGE_FONT and not gf.name_field(r["id"]):
            # the 2D-mode message windows (top screen 1:1, lower screen condensed): same line width
            d["windows_2d"] = [{"font": f, "width": w} for f, w in gamefont.MESSAGE_2D]
        if r["translation"]:
            tm = measure(r["translation"], font)
            problems = validate(r["text"], r["translation"], r["origin"] == "pc" if "origin" in r.keys() else False)
            missing = sorted({c for c in r["translation"] if ord(c) >= 0x20 and ord(c) not in font.cmap})
            if missing:
                problems.append("characters not in the game font: " + " ".join(missing))
            if cap:
                need = (len(encode_str(r["translation"])) + 1) * 2
                if need > cap:
                    problems.append(f"too long for this executable slot: max {cap // 2 - 1} characters")
            too_wide = tm["max_width"] > box["width"]
            too_many = tm["lines"] > box["lines"]
            if too_wide:
                problems.append(f"too wide for its window ({tm['max_width']}px > {box['width']}px)")
            if too_many:
                problems.append(f"more lines than its window has ({tm['lines']} > {box['lines']})")
            if not (too_wide or too_many) and not gf.fits(r["id"], r["text"], r["translation"]):
                too_wide = True
                problems.append("too wide for the 2D-mode message window")
            d.update(tr_width=tm["max_width"], tr_lines=tm["lines_per_page"], too_wide=too_wide or too_many,
                     problems=problems)
        return d

    @app.get("/api/string/<int:sid>")
    def string_detail(sid):
        db = project.db()
        r = db.execute("SELECT s.*, (SELECT COUNT(*) FROM entry WHERE source_id=s.id) AS occurrences"
                       " FROM source s WHERE id=?", (sid,)).fetchone()
        if not r:
            return jsonify({"error": "not found"}), 404
        occ = [dict(x) for x in db.execute(
            "SELECT id, kind, file, entry, member, loc, capacity, category, context, label, override"
            " FROM entry WHERE source_id=? ORDER BY id LIMIT 200", (sid,)).fetchall()]
        want_cat, want_file = request.args.get("category"), request.args.get("file")
        first = next((o for o in occ if (not want_cat or o["category"] == want_cat)
                      and (not want_file or o["file"] == want_file)), occ[0] if occ else None)
        neighbors = []
        if first:
            fe = db.execute("SELECT * FROM entry WHERE id=?", (first["id"],)).fetchone()
            neighbors = [dict(x) for x in db.execute(
                "SELECT e.id AS entry_id, e.seq, s.id, s.text, s.translation, s.status FROM entry e"
                " JOIN source s ON s.id = e.source_id WHERE e.file=? AND IFNULL(e.entry,'')=IFNULL(?, '')"
                " AND IFNULL(e.member,'')=IFNULL(?, '') AND e.seq BETWEEN ? AND ? ORDER BY e.seq",
                (fe["file"], fe["entry"], fe["member"], fe["seq"] - 6, fe["seq"] + 6)).fetchall()]
            for n in neighbors:
                n["current"] = n["entry_id"] == first["id"]
        font = main_font()
        d = _item(r, db.execute("SELECT kind, file, entry, category, context, capacity, loc FROM entry WHERE id=?",
                                (first["id"],)).fetchone(), font) if first else dict(r)
        d.update(occurrences_list=occ, neighbors=neighbors, pc=pcref.suggestions(project, sid),
                 game_font=game_fonts().entry_font(first) if first else gamefont.MESSAGE_FONT)
        return jsonify(d)

    @app.post("/api/string/<int:sid>")
    def save_string(sid):
        body = request.json or {}
        db = project.db()
        r = db.execute("SELECT * FROM source WHERE id=?", (sid,)).fetchone()
        if not r:
            return jsonify({"error": "not found"}), 404
        tr = body.get("translation", r["translation"])
        if tr is not None:
            tr = tr.replace("\r\n", "\n")
            if tr.strip() == "":
                tr = None
        status = body.get("status")
        if status is None:
            status = r["status"]
            if tr and status == 0:
                status = 2
            if not tr:
                status = 0
        note = body.get("note", r["note"])
        # a hand-edited text is the translator's: a later PC re-match must not replace it
        origin = r["origin"] if "origin" in r.keys() else None
        if (tr or "") != (r["translation"] or ""):
            origin = "manual" if tr else None
        db.execute("UPDATE source SET translation=?, status=?, note=?, origin=?, updated=? WHERE id=?",
                   (tr, int(status), note, origin, time.time(), sid))
        db.commit()
        r = db.execute("SELECT s.*, (SELECT COUNT(*) FROM entry WHERE source_id=s.id) AS occurrences"
                       " FROM source s WHERE id=?", (sid,)).fetchone()
        e = db.execute("SELECT kind, file, entry, category, context, capacity, loc FROM entry WHERE source_id=?"
                       " ORDER BY id LIMIT 1", (sid,)).fetchone()
        return jsonify(_item(r, e, main_font()))

    @app.post("/api/string/<int:sid>/box")
    def save_box(sid):
        """Window size set by hand ({width, lines}; null = automatic). For layout panes the
        exporter widens the pane to this width."""
        body = request.json or {}
        def num(k, lo, hi):
            v = body.get(k)
            return None if v in (None, "", 0) else max(lo, min(hi, int(v)))
        db = project.db()
        db.execute("UPDATE source SET box_width=?, box_lines=?, updated=? WHERE id=?",
                   (num("width", 8, 1000), num("lines", 1, 30), time.time(), sid))
        db.commit()
        r = db.execute("SELECT s.*, (SELECT COUNT(*) FROM entry WHERE source_id=s.id) AS occurrences"
                       " FROM source s WHERE id=?", (sid,)).fetchone()
        e = db.execute("SELECT kind, file, entry, category, context, capacity, loc FROM entry WHERE source_id=?"
                       " ORDER BY id LIMIT 1", (sid,)).fetchone()
        return jsonify(_item(r, e, main_font()))

    @app.post("/api/entry/<int:eid>/override")
    def save_override(eid):
        text = (request.json or {}).get("text") or None
        db = project.db()
        db.execute("UPDATE entry SET override=? WHERE id=?", (text, eid))
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/wrap")
    def do_wrap():
        body = request.json or {}
        if body.get("id") and not body.get("width"):   # the window and font the string is shown in
            return jsonify({"text": game_fonts().wrap(int(body["id"]), body.get("source", ""),
                                                      body.get("text", ""))})
        font = fonts().fonts.get(body.get("font") or "3D14", main_font())
        width = body.get("width")
        return jsonify({"text": wrap(body.get("text", ""), body.get("source", ""), font,
                                     width=width, lines_per_page=body.get("lines"))})

    @app.post("/api/measure")
    def do_measure():
        body = request.json or {}
        font = fonts().fonts.get(body.get("font") or "3D14", main_font())
        return jsonify(measure(body.get("text", ""), font))

    # ------------------------------------------------------------------ fonts for the preview
    @app.get("/api/font/<name>/meta")
    def font_meta_ep(name):
        fs = fonts()
        if name not in fs.raw:
            return jsonify({"error": "unknown font"}), 404
        return jsonify({**font_meta(fs.raw[name]), "key": fs.key})

    @app.get("/api/font/<name>/sheet<int:idx>.png")
    def font_sheet(name, idx):
        fs = fonts()
        if name not in fs.raw:
            return Response(status=404)
        cache = os.path.join(project.workdir, "cache")
        os.makedirs(cache, exist_ok=True)
        path = os.path.join(cache, f"font_{name}_{fs.key}_{idx}.png")
        if not os.path.exists(path):
            with open(path, "wb") as fp:
                fp.write(sheet_png(fs.raw[name], idx))
        return send_from_directory(cache, os.path.basename(path), max_age=3600)

    # ------------------------------------------------------------------ glossary
    @app.get("/api/glossary")
    def glossary_get():
        from . import mt
        return jsonify(mt.glossary(project))

    @app.post("/api/glossary")
    def glossary_set():
        items = request.json or []
        db = project.db()
        db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('glossary', ?)", (json.dumps(items),))
        db.commit()
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ import / export of translations
    @app.get("/api/translations/export")
    def tr_export():
        fmt = request.args.get("format", "json")
        only = request.args.get("only", "translated")
        db = project.db()
        cond = "WHERE translation IS NOT NULL" if only == "translated" else ""
        rows = db.execute(f"SELECT id, text, translation, status, note FROM source {cond} ORDER BY id").fetchall()
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "japanese", "english", "status", "note"])
            for r in rows:
                w.writerow([r["id"], r["text"], r["translation"] or "", STATUS_NAMES[r["status"]], r["note"] or ""])
            data = "﻿" + buf.getvalue()
            return Response(data, mimetype="text/csv",
                            headers={"Content-Disposition": "attachment; filename=dq11_translation.csv"})
        data = json.dumps([{"japanese": r["text"], "english": r["translation"], "status": STATUS_NAMES[r["status"]],
                            "note": r["note"]} for r in rows], ensure_ascii=False, indent=1)
        return Response(data, mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=dq11_translation.json"})

    @app.post("/api/translations/import")
    def tr_import():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400
        overwrite = request.form.get("overwrite") == "1"
        raw = f.read().decode("utf-8-sig")
        names = {v: k for k, v in STATUS_NAMES.items()}
        items = []
        if f.filename.lower().endswith(".csv"):
            for row in csv.DictReader(io.StringIO(raw)):
                items.append((row.get("japanese", ""), row.get("english", ""), row.get("status", ""), row.get("note")))
        else:
            for it in json.loads(raw):
                items.append((it.get("japanese", ""), it.get("english", ""), it.get("status", ""), it.get("note")))
        db = project.db()
        n = skipped = 0
        for jp, en, st, note in items:
            if not en:
                continue
            r = db.execute("SELECT id, translation FROM source WHERE text=?", (jp,)).fetchone()
            if not r or (r["translation"] and not overwrite):
                skipped += 1
                continue
            db.execute("UPDATE source SET translation=?, status=?, note=COALESCE(?, note), updated=? WHERE id=?",
                       (en, names.get(st, 2) or 2, note, time.time(), r["id"]))
            n += 1
        db.commit()
        return jsonify({"imported": n, "skipped": skipped})

    # ------------------------------------------------------------------ machine translation (drafts)
    @app.get("/api/mt/backends")
    def mt_backends():
        from . import mt
        return jsonify(mt.available_backends(project))

    @app.get("/api/mt/settings")
    def mt_settings_get():
        from . import mt
        return jsonify({"settings": mt.load_settings(project), "presets": mt.PRESETS})

    @app.post("/api/mt/settings")
    def mt_settings_set():
        from . import mt
        return jsonify({"settings": mt.save_settings(project, request.json or {})})

    @app.post("/api/mt/models")
    def mt_models():
        from . import mt
        body = request.json or {}
        try:
            return jsonify({"models": mt.list_models(project, body.get("base_url", ""), body.get("api_key"))})
        except Exception as e:
            return jsonify({"error": f"could not list models: {e}"}), 400

    @app.post("/api/mt/test")
    def mt_test():
        from . import mt
        body = request.json or {}
        try:
            return jsonify({"results": mt.test_backend(project, body.get("settings") or {}, body.get("api_key"))})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/mt")
    def mt_run():
        from . import mt
        body = request.json or {}
        ids = body.get("ids")
        scope = body.get("scope")  # optional: {"category": ...}
        overwrite = bool(body.get("overwrite"))
        try:
            jobs.start("translate", lambda prog, cancel: mt.translate_sources(
                project, ids=ids, scope=scope, overwrite=overwrite, progress=prog, cancel=cancel))
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 409
        return jsonify({"ok": True})

    @app.get("/api/mt/too-wide")
    def mt_too_wide():
        from . import mt
        return jsonify({"count": mt.count_too_wide(project, request.args.get("category") or None)})

    @app.post("/api/job/cancel")
    def job_cancel():
        jobs.cancel.set()
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ build
    @app.get("/api/build/defaults")
    def build_defaults():
        o = ExportOptions()
        out = project.config.get("output_dir") or os.path.join(project.workdir, "build")
        return jsonify({"options": o.__dict__, "output_dir": out})

    @app.post("/api/build")
    def build():
        body = request.json or {}
        opts = ExportOptions()
        for k, v in (body.get("options") or {}).items():
            if hasattr(opts, k):
                setattr(opts, k, type(getattr(opts, k))(v))
        out = body.get("output_dir") or os.path.join(project.workdir, "build")
        project.config["output_dir"] = out
        project.save_config()
        try:
            jobs.start("build", lambda prog, cancel: run_export(project, out, opts, prog).as_dict())
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 409
        return jsonify({"ok": True})

    @app.post("/api/open-folder")
    def open_folder():
        path = (request.json or {}).get("path") or project.workdir
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.isdir(path) and os.name == "nt":
            os.startfile(path)
        return jsonify({"ok": True})

    return app


def main():
    import argparse
    import webbrowser
    ap = argparse.ArgumentParser(description="Dragon Quest XI (3DS) translation tool")
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(HERE), "workspace"))
    ap.add_argument("--port", type=int, default=8311)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    app = create_app(args.workdir)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"DQXI translation tool running at {url}  (workspace: {args.workdir})")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
