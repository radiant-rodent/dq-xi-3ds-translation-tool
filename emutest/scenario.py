"""scenario.py - one test event per run, no walking: the hero spawns facing the Cobblestone
priest, one A press starts the configured event, every text the game shows is logged.

    python emutest/scenario.py battle LABEL --enemies "Slime x3, Dracky" [--zone 0x562]
                               [--mode 2d|3d|2way] [--party asis|strong|godlike] [--members 1,2]
    python emutest/scenario.py shop LABEL --id 1 [--mode 2d]
    python emutest/scenario.py church LABEL [--mode 3d]       # the real priest (no preset)
    python emutest/scenario.py inn LABEL
    python emutest/scenario.py steps LABEL --steps "StartUI 3 -1; WaitMenu; EndUI 3" --inputs "A,wait:2,B"
    python emutest/scenario.py suite emutest/scenarios/*.toml     # several runs + summary.html
    python emutest/scenario.py zones | monsters [QUERY]           # what can be chosen
    ... --rename "Veroni=Veronica,Sylv.=Sylvando"   # stored party names, as a new game stores them

How (details in docs/AGENT_GUIDE.md, "Scenario tests"):
- spawn: saves user_citra_2d / user_citra_3d / user_citra (2way: 3D top + 2D bottom); slot 1
  resumes at the Cobblestone church altar, facing the priest (SAVES).
- event: dq11tool/testroom.py replaces the area script's talk handler, so talking to the
  priest runs IE_TEST: party preset (AddPlayerParam / PartyAdd), then the event's calls.
- battle: dq11tool/battleparam.py rewrites scenario battle 25 (monsters, battle zone), the
  event calls LoadScenarioBattleEncount(25).
- text: build option test_text_log (dq11tool/textlog.py) + emutest/textrec.py; report.html
  (formatting visible, game-time stamps, screenshots) by emutest/report.py.

Output: _emutest/runs/scenario/LABEL/ (events.jsonl, events.txt, report.html, shots/, meta.json).
Exit code 0 = ok, 1 = crash, 2 = invalid (event did not start), 3 = text records lost.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)
import az          # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "scenario")
RUN_SAVE = "_scenario"
# one save per display mode, all resuming at the Cobblestone priest. user_citra is the user's
# save (mode 2: 3D top + 2D bottom, the early-game layout); the 2D and 3D ones were made from it
# with the game's own switch (common.SwitchDispMode, testroom mode=0/1) and a Confession save
# (2026-10-13/14). Patching only the mode byte (Data0+0x6FCC) is not enough: in 3D the hero
# then can neither walk nor talk.
MODES = {"2d": 0, "3d": 1, "2way": 2}
SAVES = {"2d": "user_citra_2d", "3d": "user_citra_3d", "2way": "user_citra"}
MODE_AT = 0x6FCC
DB = os.path.join(REPO, "workspace", "project.db")

# AddPlayerParam(member, param, amount): 1 max HP, 3 max MP, 5 magical might, 7 magical mending,
# 9 strength (seen on the Stat screen 2026-10-14; the game caps at 999). Param 11 aborts the
# event script (nothing after it runs), so it is not used.
PARTY = {
    "asis": {},
    "strong": {1: 300, 3: 100, 5: 100, 7: 100, 9: 120},
    "godlike": {1: 999, 3: 999, 5: 999, 7: 999, 9: 999},
}

# default inputs after the event started (A = the talk). Tokens: button names, "wait:S",
# "shot[:NAME]", "battle" (fight with A until the battle is over)
INPUTS = {
    "battle": "battle",
    # Buy -> browse the list (item details on the bottom screen), back, Sell -> bag, back, Quit
    "shop": "wait:1.5,shot:greeting,A,wait:1.5,shot:buy," + "DOWN,wait:0.8,shot," * 3 + "UP,UP,UP,B,wait:1,"
            "DOWN,A,wait:1.5,shot:sell,DOWN,wait:0.8,shot,B,wait:1,DOWN,A,wait:1.5,shot:quit,A,A",
    # greeting, the whole menu (cursor), Purification, Benediction, Quit
    "church": "wait:1.5,A,wait:1.5,shot:menu," + "DOWN,wait:0.5," * 6 + "shot," + "UP," * 6 +
              "DOWN,DOWN,A,wait:1.5,shot:purification,A,A,A,wait:1,DOWN,A,wait:1.5,shot:benediction,A,A,A,B,B,A,A",
    # greeting, price + Yes, the night passes (field reload); no press afterwards (it would talk
    # to the priest again)
    "inn": "wait:1.5,A,wait:1.5,shot:price,A,wait:1.5,shot:stay,wait:10,shot:morning",
    "steps": "wait:2,shot",
}
END_RE = re.compile(r"experience\s+point|wiped out|annihilat|ran away|escaped", re.I)   # battle over


# ----------------------------------------------------------------------------- build
def event_steps(a) -> list:
    steps = []
    members = [int(m) for m in filter(None, (a.members or "").split(","))]
    for m in members:
        steps.append(("PartyAdd", m))
    for member in range(1 + len(members)):
        for param, amount in PARTY[a.party].items():
            steps.append(("AddPlayerParam", member, param, amount))
    if a.party != "asis" or a.members:
        steps.append(("AllMaxHealFunc",))       # base.pack: full HP / MP for the whole party
    if a.kind == "battle":
        from dq11tool import battleparam
        steps.append(("LoadScenarioBattleEncount", battleparam.TEST_ENTRY))
    elif a.kind == "shop":
        steps += [("StartUI", 2, a.id), ("WaitMenu",), ("EndUI", 2)]
    elif a.kind == "church":
        steps = []      # the priest's own talk (greeting + church menu): no test event at all
    elif a.kind == "inn":
        steps.append(("ChartINN",))
    elif a.kind == "steps":
        steps += parse_steps(a.steps)
    return steps


def parse_steps(spec: str) -> list:
    """"StartUI 3 -1; WaitMenu; EndUI 3" -> [("StartUI", 3, -1), ...]; an argument "Name()"
    is a nested call without arguments ("LoadField GetLevelMapID() 256 3")."""
    out = []
    for part in filter(None, (p.strip() for p in (spec or "").split(";"))):
        name, *args = part.split()
        conv = []
        for x in args:
            if re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", x):
                conv.append(int(x, 0))
            elif re.fullmatch(r"\w+\(\)", x):          # GetLevelMapID(): nested call, its result
                conv.append((x[:-2],))
            elif x in ("true", "false"):
                conv.append(x == "true")
            else:
                conv.append(x.strip('"'))
        out.append((name, *conv))
    return out


def monsters_table():
    from dq11tool import battleparam, pack
    import build_test
    p = pack.Pack.parse(build_test.original(battleparam.PACK))
    eb = next(e.data for e in p.entries if e.name == battleparam.ENEMIES)
    sb = next(e.data for e in p.entries if e.name == battleparam.MEMBER)
    return battleparam.enemy_names(eb, DB), sb


def build(a, steps):
    import build_test
    from dq11tool import battleparam, testroom
    patches = {}
    if steps:       # church: the unchanged script, the A press talks to the real priest
        patches[testroom.AREA_SCRIPT] = lambda d: testroom.patch_pack(d, steps)
    if a.kind == "battle":
        names, _ = monsters_table()
        party = battleparam.parse_party(names, a.enemies)
        zone = int(a.zone, 0) if a.zone else None
        patches[battleparam.PACK] = lambda d: battleparam.patch_pack(d, party, zone)
        a.monsters = [(names.get(e, ("?", "?"))[1] or names.get(e, ("?", "?"))[0], c) for e, c in party]
    if a.kind == "shop":            # the shopkeeper's record is the NPC's: every record becomes shop --id
        from dq11tool import shopparam
        patches[shopparam.PACK] = lambda d: shopparam.patch_pack(d, a.id)
    only = None
    if getattr(a, "only", None):        # bisecting: only RomFS paths containing one of these
        keys = a.only.split(",")
        only = lambda p: any(k and k in p for k in keys)     # noqa: E731
    out = build_test.build(test_text_log=True, patches=patches, only=only, **build_opts(a))
    if getattr(a, "pure_code", False):  # bisecting: the Japanese executable + test hook + text log only
        from dq11tool import grammar, memfix, testhook, textlog
        code = open(os.path.join(az.ROOT, "code_jp.bin"), "rb").read()
        code, _ = grammar.patch(code)   # the text log hooks into it; no effect on Japanese text
        code = memfix.reserve_tail(textlog.patch(testhook.apply(code)), textlog.RING_RESERVE)
        build_test.write_code(code)
    return out


def build_opts(a) -> dict:
    """--opt NAME=VALUE (repeatable): ExportOptions overrides for experiments
    (python literals: --opt latin_keyboard=False --opt min_label_squeeze=0.7)."""
    import ast
    out = {}
    for o in getattr(a, "opt", None) or []:
        k, _, v = o.partition("=")
        try:
            out[k.strip()] = ast.literal_eval(v.strip())
        except (ValueError, SyntaxError):
            out[k.strip()] = v.strip()
    return out


GOLD_AT = (0x3E28, 0x3E2C)     # u32 gold in slot 1 (both copies; 57 in the base saves)


NAME_FIELD = 14                # bytes: a party member's name in the save (6 units + NUL)


def rename_members(d: bytearray, spec: str) -> int:
    """--rename "Veroni=Veronica,Sylv.=Sylvando": every stored member name OLD becomes NEW,
    or NEW's namefix code when NEW is longer than the save's field (as a new game stores it)."""
    import sqlite3
    from dq11tool import namefix
    codes = namefix.project_codes(sqlite3.connect(DB))
    n = 0
    for pair in filter(None, spec.split(",")):
        old, new = pair.split("=", 1)
        new = codes.get(new, new)
        if len(new) > namefix.MAX_UNITS:
            raise ValueError(f"--rename: {new!r} has no namefix code and does not fit the save")
        key = old.encode("utf-16-le")
        if len(key) < NAME_FIELD:
            key += b"\0\0"          # a 7-letter name ("Hendrik") fills the field, no NUL
        put = (new.encode("utf-16-le") + b"\0" * NAME_FIELD)[:NAME_FIELD]
        i = d.find(key)
        while i >= 0:
            d[i:i + NAME_FIELD] = put
            n += 1
            i = d.find(key, i + NAME_FIELD)
    return n


def make_save(mode: str, gold: int | None = None, rename: str = ""):
    """The run's save: a copy of the mode's base save (checked: its mode byte matches),
    optionally with more gold (there is no script call that adds gold) and renamed members."""
    import roomscan
    dst = os.path.join(az.SAVES, RUN_SAVE)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(az.SAVES, SAVES[mode]), dst)
    p = os.path.join(dst, "00000001", "Data0")
    d = bytearray(open(p, "rb").read())
    if d[MODE_AT] != MODES[mode]:
        raise RuntimeError(f"save {SAVES[mode]} is in display mode {d[MODE_AT]}, not {mode}")
    if gold is not None:
        for at in GOLD_AT:
            struct.pack_into("<I", d, at, gold)
    if rename:
        print("renamed", rename_members(d, rename), "stored names")
    if gold is not None or rename:
        open(p, "wb").write(roomscan.fix_crc(d))


# ----------------------------------------------------------------------------- run
class Run:
    def __init__(self, a, out):
        import azctl
        import textrec
        self.a, self.out = a, out
        self.s = azctl.Session()
        self.rec = textrec.TextRecorder(self.s, out)
        self.crash = None
        self.n_shot = 0

    def boot(self):
        """Driven by the text log (fixed timings once ended in the title's Save Code screen):
        A through title, Continue and slot 1 until the first message window (txt1: "View the
        story so far?"), B (= No) until the field is loaded (the bottom screen's place label
        txt_place), then B for the priest's Continue blessing. After that no button is
        pressed until the talk."""
        self.crash = self.s.wait_game(6)
        self.rec.start()

        def seen(name, since=0):
            """a record of pane `name`, not part of a layout being created (dozens of panes
            get their file placeholder texts in one frame)."""
            ev = [e for e in self.rec.events if "seq" in e]
            per_frame = {}
            for e in ev:
                per_frame[e["frame"]] = per_frame.get(e["frame"], 0) + 1
            return any(e.get("name") == name and per_frame[e["frame"]] < 12
                       for e in self.rec.events[since:] if "seq" in e)

        for _ in range(60):
            if self.crash or seen("txt1"):
                break
            self.press("A", after=1.4)
        else:
            self.crash = self.crash or "boot: no Continue prompt"
        for _ in range(40):
            if self.crash or seen("txt_place"):
                break
            self.press("B", after=1.0)
        else:
            self.crash = self.crash or "boot: the field never loaded"
        mark = len(self.rec.events)
        for _ in range(10):                         # the blessing comes a few seconds later
            self.crash = self.crash or self.s.wait_game(self.s.game_time() + 1.0)
            if seen("txt1", mark):
                break
        self.crash = self.crash or self.s.wait_game(self.s.game_time() + 2.0)
        self.press("B", after=0.5)
        self.press("B", after=2.0)

    def press(self, b: str, after: float = 1.0):
        self.crash = self.crash or self.s.press(b, after=after) or self.s.crashed()

    def shot(self, name: str = ""):
        self.n_shot += 1
        self.rec.shot(f"step{self.n_shot:02d}" + ("_" + name if name else ""))

    def battle(self, max_presses: int = 80):
        """A (Fight / Attack / first target, then the messages) until a result line
        (experience / wiped out / fled) appeared, then A through the result messages until the
        field is back (its place label txt_place is set again). No press after that: it would
        talk to the priest again and start the next battle."""
        start, result = len(self.rec.events), None
        for _ in range(max_presses):
            if self.crash:
                return
            if result is None:
                if any(END_RE.search(e.get("text", "")) for e in self.rec.events[start:]):
                    result = len(self.rec.events)
                    self.shot("result")
            if result is not None:
                self.crash = self.crash or self.s.wait_game(self.s.game_time() + 1.5)
                if any(e.get("name") == "txt_place" for e in self.rec.events[result:]):
                    break
            self.press("A", after=1.4)
        else:
            self.crash = self.crash or "battle: no result after %d presses" % max_presses
        self.crash = self.crash or self.s.wait_game(self.s.game_time() + 2.0)
        self.shot("after_battle")

    def inputs(self, spec: str):
        for tok in filter(None, (t.strip() for t in spec.split(","))):
            if self.crash:
                return
            if tok == "battle":
                self.battle()
            elif tok.startswith("wait:"):
                self.crash = self.s.wait_game(self.s.game_time() + float(tok[5:]))
            elif tok.startswith("hold:"):              # hold:DOWN:0.4 (D-pad walk: 2way talk on the 2D screen)
                _, b, sec = tok.split(":")
                self.crash = self.s.hold(b, float(sec)) or self.s.crashed()
            elif tok.startswith("shot"):
                self.shot(tok[5:] if ":" in tok else "")
            else:
                self.press(tok)


def run(a) -> int:
    label = a.label
    out = os.path.join(OUT, label)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    import azctl
    import report
    steps = event_steps(a)
    meta = {"label": label, "kind": a.kind, "mode": a.mode, "party": a.party,
            "steps": "; ".join(" ".join(str(x) for x in s) for s in steps),
            "date": time.strftime("%Y-%m-%d %H:%M")}
    t0 = time.time()
    r = None
    try:
        b = build(a, steps)
        if a.kind == "battle":
            meta["monsters"] = ", ".join(f"{n.replace(chr(10), ' ')} x{c}" for n, c in a.monsters)
            meta["zone"] = a.zone or "default"
        # inn: the priest charges 999 G; 5000 G or more on load earns the "Gold Magnet"
        # accolade (a popup at spawn), so stay below
        gold = a.gold if a.gold is not None else (4999 if a.kind == "inn" else None)
        make_save(a.mode, gold, a.rename)
        meta["gold"] = gold if gold is not None else "save (57)"
        if os.path.exists(azctl.STATE) and _alive():
            azctl.main(["stop"])
        azctl.main(["start", "--save", RUN_SAVE])
        r = Run(a, out)
        r.boot()
        r.shot("spawn")
        if a.pre:                                   # e.g. "Y,wait:2": 2D mode, swap the screens
            r.inputs(a.pre)
        before, t_talk = len(r.rec.events), r.s.game_time()
        if getattr(a, "burst", 0):                  # screenshots every 0.4 s (transitions)
            r.press("A", after=0.1)
            for k in range(a.burst):
                r.crash = r.crash or r.s.wait_game(r.s.game_time() + float(os.environ.get("BURST_DT", "0.4")))
                r.shot(f"burst{k:02d}")
        else:
            r.press("A", after=4.0)                 # talk to the priest: the test event starts
        r.shot("event")
        r.inputs(a.inputs or INPUTS[a.kind])
        r.rec.stop()
        # the event started if any text came within 15 game seconds of the talk (the field is
        # quiet otherwise; 2D layouts can take several seconds to load)
        started = any("seq" in e and e["t"] is not None and e["t"] <= t_talk + 15
                      for e in r.rec.events[before:])
        crash = r.crash or r.s.crashed()
        meta.update(result="CRASH" if crash else ("ok" if started else "INVALID: no text after the talk"),
                    crash=crash or "", records=r.rec.count, lost=r.rec.dropped,
                    build_s=b.get("seconds"), run_s=round(time.time() - t0))
    except (ConnectionError, OSError) as e:     # the emulator process went away
        alive = _alive()
        rc = r.s.proc.poll() if r and getattr(r.s, "proc", None) else None
        how = "not responding" if alive else ("exited" if rc is None else f"exited (code {rc:#x})")
        meta.update(result="CRASH", crash=f"emulator {how}: {e}",
                    records=r.rec.count if r else 0, lost=r.rec.dropped if r else 0,
                    run_s=round(time.time() - t0))
        if r:
            try:
                r.rec._stop.set()
                r.rec._fp.close()
            except Exception:
                pass
    finally:
        if not a.keep:
            try:
                azctl.main(["stop"])
            except Exception:
                pass
    json.dump(meta, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    path = report.build(out)
    shots = sorted(glob.glob(os.path.join(out, "shots", "*step*.png")))
    if shots:
        az.sheet(shots, os.path.join(out, "sheet.png"), cols=4, scale=0.5)
    print(f"{label}: {meta['result']} - {meta.get('records')} records, {meta.get('lost')} lost -> {path}")
    if meta["result"] == "CRASH":
        return 1
    if meta["result"] != "ok":
        return 2
    return 3 if meta.get("lost") else 0


def _alive():
    import azctl
    try:
        return azctl.Session().alive()
    except Exception:
        return False


# ----------------------------------------------------------------------------- CLI
def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kind", choices=["battle", "shop", "church", "inn", "steps", "suite", "zones", "monsters"])
    ap.add_argument("label", nargs="?", default=None)
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--mode", choices=list(MODES), default="3d")
    ap.add_argument("--party", choices=list(PARTY), default="strong")
    ap.add_argument("--members", default="", help="party members to add (PartyAdd ids), e.g. 1,2")
    ap.add_argument("--enemies", default="Slime x3")
    ap.add_argument("--zone", default=None, help="battle zone (hex/int, see `zones`)")
    ap.add_argument("--id", type=int, default=1, help="shop: fieldcmn shop.bxon record (shopparam copies it over all)")
    ap.add_argument("--steps", default="", help="native calls for kind=steps")
    ap.add_argument("--inputs", default=None, help="override the inputs after the talk")
    ap.add_argument("--pre", default="", help="inputs before the talk (2D: Y,wait:2 swaps the screens)")
    ap.add_argument("--gold", type=int, default=None, help="gold in the save (inn: 4999; >= 5000 earns an accolade at load)")
    ap.add_argument("--rename", default="", help="stored member names OLD=NEW,... (NEW longer than 6: its namefix code)")
    ap.add_argument("--keep", action="store_true", help="leave the emulator running")
    ap.add_argument("--pure-code", action="store_true", help="bisect: unpatched executable (test hook, text log, grammar pass)")
    ap.add_argument("--burst", type=int, default=0, help="screenshots every 0.4 s after the talk (N shots)")
    ap.add_argument("--only", default=None, help="bisect: keep only RomFS changes whose path contains one of these (comma list; ',' = none)")
    ap.add_argument("--opt", action="append", default=[], help="ExportOptions override NAME=VALUE (repeatable)")
    return ap


def suite(files: list[str]) -> int:
    """Run every [[run]] of the TOML files; summary.html links the reports."""
    import tomllib
    runs, worst = [], 0
    for f in files:
        for r in tomllib.load(open(f, "rb")).get("run", []):
            argv = [r.pop("kind"), r.pop("label")]
            for k, v in r.items():
                argv += [f"--{k}", str(v)] if not isinstance(v, bool) else ([f"--{k}"] if v else [])
            a = parser().parse_args(argv)
            try:
                code = run(a)
            except Exception as e:              # a broken run must not stop the suite
                print(f"{a.label}: ERROR {type(e).__name__}: {e}", flush=True)
                code = 2
            runs.append((a.label, code))
            worst = max(worst, code)
    rows = "".join(f"<tr><td><a href='{l}/report.html'>{l}</a></td><td>{['ok', 'CRASH', 'INVALID', 'LOST TEXT'][c]}</td></tr>"
                   for l, c in runs)
    with open(os.path.join(OUT, "summary.html"), "w", encoding="utf-8") as fp:
        fp.write(f"<!doctype html><meta charset='utf-8'><title>Scenario suite</title><style>body{{font:14px system-ui;"
                 f"padding:16px}}td{{padding:4px 12px;border-bottom:1px solid #ddd}}</style><h1>Scenario suite"
                 f"</h1><table>{rows}</table>")
    print("summary:", os.path.join(OUT, "summary.html"))
    return worst


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.kind == "suite":
        return suite(([a.label] if a.label else []) + a.rest)
    if a.kind == "monsters":
        names, _ = monsters_table()
        q = (a.label or "").lower()
        for eid, (jp, en) in sorted(names.items()):
            if q in en.lower() or q in jp:
                print(f"{eid:5} {en.replace(chr(10), ' '):40} {jp.replace(chr(10), ' ')}")
        return 0
    if a.kind == "zones":
        from dq11tool import battleparam
        names, sb = monsters_table()
        n, _ = battleparam.records(sb)
        zones = {}
        for k in range(n):
            r = battleparam.read(sb, k)
            mons = ", ".join((names.get(e, ("?", "?"))[1] or names.get(e, ("?", "?"))[0]).replace("\n", " ")
                             for e, _ in r["monsters"][:3])
            zones.setdefault(r["zone"], f"scenario battle {k}: {mons}")
        for z, ex in sorted(zones.items()):
            print(f"{z:#06x}  {ex}")
        return 0
    if not a.label:
        parser().error("LABEL is required")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
