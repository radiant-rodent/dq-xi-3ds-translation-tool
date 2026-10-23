"""report.py OUT - turn a text recording (emutest/textrec.py: OUT/events.jsonl + OUT/shots/)
into OUT/report.html (timeline of every text shown, formatting visible, screenshots) and
OUT/events.txt (one line per message, for grep / diffs).

Grouping (the log has one record per SetString call):
- typed text: a message window sets its pane once per typed character; consecutive records of a
  pane that extend the previous text are one message (start = first character, done = last).
- twin panes: the same text in several panes in the same frame (text + shadow/outline panes)
  is one message listing all pane names.
- layout init: when a layout is created, its panes get their file placeholder texts in one
  frame ("Test message. This should never appear."): texts set in such a burst that are a
  layout file's own text (bflyt entries of the project DB) are folded (checkbox); real texts
  set in the same frame (menu items) stay visible; texts without letters (numbers, symbols)
  in such a burst count as placeholders too. events.txt keeps them, tagged [layout].
Checks per message: line widths in px with the message font (gamefont.MESSAGE_FONT); typed
messages (message windows) wider than gamefont.MESSAGE_WINDOW or with more than
MESSAGE_LINES lines per page are flagged.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

BURST = 12          # distinct new panes in one frame = a layout being created
TYPE_GAP = 1.5      # game s: a typed message continues if the next character comes within this


def _load(out):
    import textrec
    return textrec.load(out)


_font = None


def _widths(shown: str) -> list[int]:
    global _font
    try:
        if _font is None:
            from dq11tool import gamefont, metrics
            from dq11tool.project import Project
            _font = metrics.FontSet.for_project(Project(os.path.join(REPO, "workspace"))).fonts[gamefont.MESSAGE_FONT]
        from dq11tool.metrics import line_widths
        return line_widths(shown, _font)
    except Exception:
        return []


_defaults = None


def layout_defaults() -> set[str]:
    """Texts stored in the layout files (bflyt panes, translated or not): what a pane shows
    right after its layout is created, before the game sets the real text."""
    global _defaults
    if _defaults is None:
        _defaults = set()
        db = os.path.join(REPO, "workspace", "project.db")
        if os.path.exists(db):
            import sqlite3
            con = sqlite3.connect(db)
            for text, tr in con.execute("SELECT s.text, s.translation FROM entry e JOIN source s "
                                        "ON s.id = e.source_id WHERE e.kind = 'bflyt'"):
                _defaults.add(text)
                if tr:
                    _defaults.add(tr)
    return _defaults


def group(events: list[dict]) -> tuple[list[dict], list[dict], int]:
    """(messages, shots, lost records)."""
    recs = [e for e in events if "seq" in e]
    shots = [e for e in events if "shot" in e and "seq" not in e]
    lost = sum(e.get("lost", 0) for e in events)
    per_frame = {}
    for r in recs:
        per_frame[r["frame"]] = per_frame.get(r["frame"], 0) + 1
    burst_frames = {f for f, n in per_frame.items() if n >= BURST}
    defaults = layout_defaults()
    msgs, open_by_pane, by_seq = [], {}, {}
    for r in recs:
        if r["phase"] == "resolved":
            m = by_seq.get(r["seq"] - 1)
            if m is not None:
                m["resolved"], m["resolved_shown"] = r["text"], r["shown"]
                by_seq[r["seq"]] = m
            continue
        m = open_by_pane.get(r["pane"])
        typed = (m is not None and r["text"].startswith(m["text"]) and len(r["text"]) > len(m["text"])
                 and r["t"] - m["t_done"] <= TYPE_GAP and len(m["text"]) > 0 or
                 m is not None and m["text"] == "" and r["t"] - m["t_done"] <= 0.1)
        if typed:
            m.update(text=r["text"], shown=r["shown"], t_done=r["t"], frame_done=r["frame"])
            m["steps"].append((r["t"], r["text"]))
            m["typed"] = len(m["steps"]) > 2
            m.pop("resolved", None), m.pop("resolved_shown", None)
        else:
            m = {"seq": r["seq"], "t": r["t"], "t_done": r["t"], "frame": r["frame"], "frame_done": r["frame"],
                 "panes": [r["name"]], "pane_ids": [r["pane"]], "text": r["text"], "shown": r["shown"],
                 "start": r["start"], "steps": [(r["t"], r["text"])], "typed": False,
                 "init": r["frame"] in burst_frames and (r["shown"] in defaults or not defaults
                                                         or not re.search(r"[^\W\d_]", r["shown"]))}
            msgs.append(m)
            open_by_pane[r["pane"]] = m
        by_seq[r["seq"]] = m
    # twin panes: same final text, same start/done frames
    out, key_idx = [], {}
    for m in msgs:
        k = (m["text"], m["frame"], m["frame_done"], m["init"])
        j = key_idx.get(k)
        if j is not None and m["panes"][0] not in out[j]["panes"]:
            out[j]["panes"] += m["panes"]
            continue
        key_idx[k] = len(out)
        out.append(m)
    for m in out:
        if not m["text"] and len(m["steps"]) == 1:
            m["clear"] = True
        final = m.get("resolved_shown", m["shown"])
        m["widths"] = _widths(final) if final.strip() else []
        m["warn"] = []
        if m["typed"]:
            from dq11tool import gamefont
            over = [w for w in m["widths"] if w > gamefont.MESSAGE_WINDOW]
            if over:
                m["warn"].append(f"line wider than the window: {max(over)} px > {gamefont.MESSAGE_WINDOW}")
            n = final.rstrip("\n").count("\n") + 1      # one message string = one window page
            if n > gamefont.MESSAGE_LINES:
                m["warn"].append(f"{n} lines in one message (the window shows {gamefont.MESSAGE_LINES})")
        m["shot"] = next((s["shot"] for s in shots if s["t"] >= m["t_done"]), None)
    return out, shots, lost


TOKEN = re.compile(r"(\{CR\}|\{PAGE\}|\{(?:the|The|a|A|pl|noun)\}|\{n:|\{v:[0-9A-F]+\}|\{c[0-9A-F]{2}\}|\{ruby:[^}]*\}|\n)")


def fmt(shown: str) -> str:
    """HTML with control codes as visible badges and real line breaks."""
    parts = []
    for p in TOKEN.split(shown):
        if not p:
            continue
        if p == "\n":
            parts.append('<span class="tk nl">⏎</span><br>')
        elif p == "{CR}":
            parts.append('<span class="tk cr">{CR}</span>')
        elif p == "{PAGE}":
            parts.append('<span class="tk cr">{PAGE}</span>')
        elif p.startswith("{v:"):
            parts.append(f'<span class="tk var">{html.escape(p)}</span>')
        elif p.startswith("{c") or p.startswith("{ruby"):
            parts.append(f'<span class="tk ctl">{html.escape(p)}</span>')
        elif p.startswith("{"):
            parts.append(f'<span class="tk gr">{html.escape(p)}</span>')
        else:
            # spaces are only marked where they matter for layout: doubled or at a line end
            e = html.escape(p)
            e = re.sub(r"  +| +$", lambda m: '<span class="sp" title="space">' + "·" * len(m.group()) + "</span>", e)
            parts.append(e)
    return "".join(parts) or '<span class="empty">(empty)</span>'


CSS = """
:root{--bg:#fff;--fg:#1d1f23;--mut:#6b7079;--line:#e3e5e9;--card:#f7f8fa;--warn:#b3261e;--warnbg:#fdecea;
--cr:#b3261e;--gr:#1a5fb4;--var:#26802f;--ctl:#a05a00;--hl:#fff4c2}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e6e7ea;--mut:#9aa0aa;--line:#2c3036;--card:#1e2126;
--warn:#ff8a80;--warnbg:#3a1f1f;--cr:#ff8a80;--gr:#8ab4f8;--var:#81c995;--ctl:#fdc675;--hl:#3b3520}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;margin:0;padding:16px}
h1{font-size:18px;margin:0 0 4px} .mut{color:var(--mut)} .bar{display:flex;gap:16px;flex-wrap:wrap;align-items:center;
margin:12px 0;position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:2}
input[type=search]{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);min-width:240px}
table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}
th{font-weight:600;color:var(--mut);font-size:12px} td.t{white-space:nowrap;font-variant-numeric:tabular-nums}
.txt{font-family:Consolas,ui-monospace,monospace;font-size:13px;background:var(--card);padding:4px 6px;border-radius:4px;display:inline-block;max-width:640px}
.tk{border-radius:3px;padding:0 3px;font-size:11px;margin:0 1px} .nl{color:var(--mut)} .cr{color:var(--cr);border:1px solid var(--cr)}
.gr{color:var(--gr);border:1px solid var(--gr)} .var{color:var(--var);border:1px solid var(--var)} .ctl{color:var(--ctl);border:1px solid var(--ctl)}
.sp{background:var(--hl)} .empty{color:var(--mut);font-style:italic}
tr.warn td{background:var(--warnbg)} .w{color:var(--warn);font-weight:600} .pane{font-family:Consolas,monospace;font-size:12px}
tr.init,tr.clear{display:none} body.showinit tr.init, body.showclear tr.clear{display:table-row}
img.thumb{width:200px;border:1px solid var(--line);border-radius:4px;cursor:zoom-in} details summary{cursor:pointer;color:var(--mut);font-size:12px}
.steps{font-family:Consolas,monospace;font-size:12px;color:var(--mut);max-height:200px;overflow:auto}
#big{position:fixed;inset:0;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center;z-index:5}
#big img{max-width:95vw;max-height:95vh;image-rendering:pixelated;width:800px}
@media (max-width:700px){img.thumb{width:120px} .txt{max-width:100%}}
"""

JS = """
const q=document.getElementById('q');
q.addEventListener('input',()=>{const v=q.value.toLowerCase();
 document.querySelectorAll('tbody tr').forEach(r=>{r.style.display=(!v||r.textContent.toLowerCase().includes(v))?'':'none'})});
document.getElementById('ci').addEventListener('change',e=>document.body.classList.toggle('showinit',e.target.checked));
document.getElementById('cc').addEventListener('change',e=>document.body.classList.toggle('showclear',e.target.checked));
const big=document.getElementById('big');
document.querySelectorAll('img.thumb').forEach(i=>i.addEventListener('click',()=>{big.firstChild.src=i.src;big.style.display='flex'}));
big.addEventListener('click',()=>big.style.display='none');
"""


def build(out: str, title: str | None = None, meta: dict | None = None) -> str:
    events = _load(out)
    msgs, shots, lost = group(events)
    meta = meta or {}
    mp = os.path.join(out, "meta.json")
    if os.path.exists(mp):
        meta = {**json.load(open(mp, encoding="utf-8")), **meta}
    title = title or meta.get("label") or os.path.basename(os.path.abspath(out))
    t0 = msgs[0]["t"] if msgs else 0
    rows, prev = [], None
    for m in msgs:
        cls = " ".join(c for c in ("warn" if m["warn"] else "", "init" if m["init"] else "",
                                   "clear" if m.get("clear") else "") if c)
        dt = "" if prev is None else f"+{m['t'] - prev:.2f}"
        prev = m["t"]
        timing = f"{m['t']:.3f}<br><span class='mut'>{dt}</span>"
        if m["typed"]:
            timing += f"<br><span class='mut'>typed {m['t_done'] - m['t']:.2f} s</span>"
        txt = f"<div class='txt'>{fmt(m['shown'])}</div>"
        if "resolved_shown" in m:
            txt = (f"<div class='mut'>raw</div>{txt}<div class='mut'>in game (after grammar pass)</div>"
                   f"<div class='txt'>{fmt(m['resolved_shown'])}</div>")
        if len(m["steps"]) > 2:
            st = "".join(f"<div>{t:.3f} {html.escape(s[-40:])!s}</div>" for t, s in m["steps"])
            txt += f"<details><summary>{len(m['steps'])} steps</summary><div class='steps'>{st}</div></details>"
        widths = " / ".join(str(w) for w in m["widths"])
        warn = "".join(f"<div class='w'>{html.escape(w)}</div>" for w in m["warn"])
        shot = f"<img class='thumb' loading='lazy' src='{html.escape(m['shot'])}'>" if m["shot"] else ""
        rows.append(f"<tr class='{cls}'><td class='t'>{timing}</td><td class='pane'>{'<br>'.join(m['panes'])}"
                    f"</td><td>{txt}{warn}</td><td class='mut'>{widths}</td><td>{shot}</td></tr>")
    n_init = sum(m["init"] for m in msgs)
    n_warn = sum(bool(m["warn"]) for m in msgs)
    info = " · ".join(f"{k}: {html.escape(str(v))}" for k, v in meta.items() if k != "label")
    lost_html = f"<span class='w'>{lost} records LOST (ring overrun)</span>" if lost else "no records lost"
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Text log {html.escape(title)}</title>
<style>{CSS}</style></head><body>
<h1>Text log: {html.escape(title)}</h1>
<div class="mut">{info}</div>
<div class="mut">{len(msgs) - n_init} messages ({n_warn} flagged) · {n_init} layout placeholder texts hidden ·
{len(shots)} screenshots · {lost_html} · times are game seconds (HID clock)</div>
<div class="bar"><input id="q" type="search" placeholder="Filter text or pane…">
<label><input id="ci" type="checkbox"> layout placeholders</label>
<label><input id="cc" type="checkbox"> clears (empty text)</label></div>
<table><thead><tr><th>game time</th><th>pane</th><th>text (⏎ newline, {{CR}} wait for button, blue = grammar tags)</th>
<th>line px</th><th>screen after</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table><div id="big"><img alt=""></div><script>{JS}</script></body></html>"""
    path = os.path.join(out, "report.html")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    with open(os.path.join(out, "events.txt"), "w", encoding="utf-8") as fp:
        for m in msgs:
            flag = " !" + "; ".join(m["warn"]) if m["warn"] else ""
            res = f"  =>  {m['resolved_shown']!r}" if "resolved_shown" in m else ""
            tag = "[layout] " if m["init"] else ""
            fp.write(f"{m['t']:10.3f} {tag}{'+'.join(m['panes']):24} {m['shown']!r}{res}{flag}\n")
    return path


if __name__ == "__main__":
    print(build(sys.argv[1]))
