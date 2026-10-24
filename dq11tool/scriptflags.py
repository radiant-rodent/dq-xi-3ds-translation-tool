"""What the event scripts do with the save's flags, for the save editor's flag names.

The game's tables do not name its global flags, record flags or cutscene flags; the compiled
Squirrel event scripts (romfs/script/*.pack, script.cnut) call GetGlobalGameFlag(n),
SetGlobalGameFlag(n, value) and friends with constant numbers. Every such call is collected with
the script's place (an area from level_area.bxon or a story chapter from scenario_id_list.bxon)
and the event it sits in, and each flag gets a short name ("Cobblestone: talk event") and a
longer note listing where it is set and checked.
"""
from __future__ import annotations

import collections
import re
import struct
from pathlib import Path

from . import sqbin
from .pack import Pack

OP = sqbin.OP

# script command -> (save table, which argument is the number)
COMMANDS = {
    "GetGlobalGameFlag": ("global", "get"), "SetGlobalGameFlag": ("global", "set"),
    "SetRecordFlag": ("record", "set"), "GetRecordFlag": ("record", "get"),
    "GetCSWatchFlag": ("cutscene", "get"), "SetCSWatchFlag": ("cutscene", "set"),
    "AddRecordCount": ("counter", "set"), "GetRecordCount": ("counter", "get"),
}

# script packs that are not an area or a chapter
SPECIAL = {
    "common": "shared story scripts", "c_com": "Part 3 shared scripts", "past": "the past (Part 3)",
    "world": "world map", "worldc": "world map (2D)", "camp": "camping", "base": "inns and shops",
    "dive": "dive dungeon", "a000_area": "area scripts",
}

# event function -> what it means
EVENTS = [
    ("OnCutSceneEnd", "after a cutscene"), ("OnBattleEnd", "after a story battle"),
    ("ScenarioToBPart", "reset when Part 2 starts"), ("ScenarioToCPart", "reset when Part 3 starts"),
    ("OnScenarioJumpInit", "chapter start"), ("OnBeginScene", "map load"), ("OnSetupScene", "map load"),
    ("OnPreSetupLevel", "map load"), ("WalkThrough", "walking through a spot"),
    ("TrgMapJumpEvent", "map exit"), ("TrgRCommand", "R-button action"), ("TrgEvent", "trigger area"),
    ("MoveFloor", "moving floor"), ("RideHorse", "riding a horse"), ("Gimmick", "map gimmick"),
    ("ItemCheck", "item check"), ("WarpSymbol", "warp point"), ("Clear", "clear event"),
]


def _ascii(d: bytes, field: int) -> str:
    t = field + struct.unpack_from("<I", d, field)[0]
    return d[t:d.index(b"\0", t)].decode("ascii", "replace")


def place_names(syscmn: Pack, names) -> dict[str, str]:
    """Script pack code -> place: areas (level_area.bxon: +12 name, +16 code, 24 bytes) and story
    chapters (scenario_id_list.bxon: +0 code, +4 name, 12 bytes). `names(entry, data, field)` gives
    the English text of a string field."""
    out = dict(SPECIAL)
    d = syscmn.get("level_area.bxon").data
    n, p = struct.unpack_from("<I", d, 0x20)[0], 0x24 + struct.unpack_from("<I", d, 0x24)[0]
    for i in range(n):
        code = _ascii(d, p + 24 * i + 16)
        if code:
            out[code] = names("level_area.bxon", d, p + 24 * i + 12)
    d = syscmn.get("scenario_id_list.bxon").data
    n, p = struct.unpack_from("<I", d, 0x20)[0], 0x24 + struct.unpack_from("<I", d, 0x24)[0]
    for i in range(n):
        out[_ascii(d, p + 12 * i)] = names("scenario_id_list.bxon", d, p + 12 * i + 4)
    return out


def _place(pack: str, places: dict[str, str]) -> str:
    if pack in places:
        return places[pack]
    m = re.match(r"([a-c]_\d{3})", pack)                 # chapter scripts and their cutscenes
    if m and m.group(1) in places:
        return places[m.group(1)]
    m = re.match(r"([a-z]\d{3})c?$", pack)              # areas; "c" = the 2D version
    if m and m.group(1) in places:
        return places[m.group(1)]
    return pack


def _event(func: str) -> str:
    last = func.split("/")[-1]
    for key, text in EVENTS:
        if key in last:
            return text
    if last.startswith("IE_"):
        if re.search(r"_Q\d{3}", last):
            return "side-quest talk"
        return "talk event"
    if last.startswith(("Trg", "On")):
        return "map event"
    return last


def _calls(func):
    lits, ins = func.literals, func.instructions
    for i, x in enumerate(ins):
        if x.op != OP["PREPCALLK"] or not 0 <= x.arg1 < len(lits):
            continue
        name = lits[x.arg1].value
        if name not in COMMANDS:
            continue
        args = {}
        for y in ins[i + 1:i + 14]:
            if y.op == OP["CALL"] and y.arg1 == x.arg0:
                break
            if y.op == OP["LOADINT"] and y.arg0 > x.arg0 + 1:
                args[y.arg0 - x.arg0 - 2] = y.arg1
            elif y.op == OP["LOADBOOL"] and y.arg0 > x.arg0 + 1:
                args[y.arg0 - x.arg0 - 2] = int(bool(y.arg1))
        if isinstance(args.get(0), int):
            yield name, args[0], args.get(1)


def _funcs(f, path=""):
    name = f.name.value if f.name is not None and isinstance(f.name.value, str) else "?"
    p = f"{path}/{name}" if path else name
    yield p, f
    for g in f.functions:
        yield from _funcs(g, p)


def scan(script_dir: Path, places: dict[str, str]):
    """table -> number -> {"set": Counter((place, event, value)), "get": Counter(place)}"""
    out = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"set": collections.Counter(), "get": collections.Counter()}))
    for path in sorted(script_dir.glob("*.pack")):
        try:
            pack = Pack.parse(path.read_bytes())
        except Exception:
            continue
        place = _place(path.stem, places)
        for e in pack.entries:
            if not e.name.endswith(".cnut"):
                continue
            try:
                root = sqbin.parse(e.data)
            except Exception:
                continue
            for fpath, f in _funcs(root):
                for name, number, value in _calls(f):
                    table, kind = COMMANDS[name]
                    use = out[table][number]
                    if kind == "set":
                        use["set"][(place, _event(fpath), value)] += 1
                    else:
                        use["get"][place] += 1
    return out


def _join(items, limit=3) -> str:
    items = list(dict.fromkeys(items))
    return ", ".join(items[:limit]) + (f" +{len(items) - limit} more" if len(items) > limit else "")


def describe(use) -> tuple[str, str]:
    """(short name, longer note) for one flag."""
    sets, gets = use["set"], use["get"]
    if sets:
        places = [p for (p, _, _), _ in sets.most_common()]
        events = [e for (_, e, _), _ in sets.most_common()]
        values = sorted({v for (_, _, v) in sets if v is not None})
        name = f"{_join(places, 2)}: {_join(events, 2)}"
        note = f"set{' to ' + '/'.join(map(str, values)) if values else ''} in {_join(places, 4)} ({_join(events, 4)})"
        if gets:
            note += f"; checked in {_join([p for p, _ in gets.most_common()], 4)}"
        return name, note
    if gets:
        places = [p for p, _ in gets.most_common()]
        return f"{_join(places, 2)}: checked only", f"never set by a script; checked in {_join(places, 4)}"
    return "", ""


def notes(workspace: Path, syscmn: Pack, names) -> dict[str, dict[int, tuple[str, str]]]:
    places = place_names(syscmn, names)
    found = scan(workspace / "extracted/romfs/script", places)
    return {table: {n: describe(u) for n, u in sorted(nums.items())} for table, nums in found.items()}
