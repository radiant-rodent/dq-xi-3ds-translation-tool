"""Write the game tables the DQXI save editor needs as one JSON file (plus icons.png).

    python -m dq11tool.savedata workspace ../dqxi-3ds-save-editor/gamedata.json

The editor lives in its own repository: https://github.com/radiant-rodent/dqxi-3ds-save-editor

The file holds item names (English from the project database, Japanese where untranslated),
item types and stats, the level tables of every character record and the names of chapters,
quests, accolades, monsters and more; icons.png next to it holds the item icons. Both are built
from the extracted game data, so they are not committed; the editor works without them.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

from .pack import Pack

ITEM_SIZE = 0x58
LEVEL_ROW = 24

# Character record n of a save slot uses the level table named here (order of the names in
# code.bin). Records 28 and 29 have no table of their own; they reuse the closest match.
RECORD_PARAMS = [
    "hero", "camu", "veronica", "sena", "sylvia", "rou", "martina", "graig", "ruki", "ema",
    "hanfuri", "sp_camu", "sp_veronica", "sp_sena", "sp_sylvia", "sp_rou", "sp_martina",
    "sp_graig", "ruko", "ekkeharuto", "dog", "kinai", "bahatora", "cheron", "teba", "sanpo",
    "erwin", "ex_sena", "sylvia", "hanfuri", "kuruchi",
]


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _root(d: bytes) -> tuple[int, int]:
    return _u32(d, 0x20), 0x24 + _u32(d, 0x24)


def _string_at(d: bytes, field: int) -> tuple[int, str]:
    t = field + _u32(d, field)
    e = t
    while d[e:e + 2] != b"\0\0":
        e += 2
    return t, d[t:e].decode("utf-16le")


def _clean(s: str) -> str:
    # Names are wrapped for the game's boxes; a line ending in "-" was split inside a word.
    out = ""
    for part in s.split("\n"):
        part = part.strip()
        if not out:
            out = part
        elif out.endswith("-"):
            out = out[:-1] + part
        else:
            out += " " + part
    return out


def _ascii_at(d: bytes, field: int) -> str:
    t = field + _u32(d, field)
    return d[t:d.index(b"\0", t)].decode("ascii", "replace")


ICON = 64               # ui/item/<name>.bch: one 64x64 texture, RGBA4 or ETC1A4
ICONS_PER_ROW = 32


def _icon_pixels(data: bytes):
    from . import texture
    from .texlabel import _fake_flim
    start = _u32(data, 0x14)                     # BCH data section
    if len(data) - start >= ICON * ICON * 2:
        return texture.decode(_fake_flim(data[start:start + ICON * ICON * 2], ICON, ICON, 8))
    return texture.decode(_fake_flim(data[start:start + ICON * ICON], ICON, ICON, 11))


def icon_sheet(workspace: Path, names: list[str], out: Path) -> dict[str, int]:
    """Every item icon in one PNG (ICONS_PER_ROW per row); returns icon name -> index."""
    import numpy as np
    from PIL import Image
    folder = workspace / "extracted/romfs/ui/item"
    index: dict[str, int] = {}
    tiles = []
    for name in names:
        if name in index or not (folder / f"{name}.bch").exists():
            continue
        try:
            tiles.append(_icon_pixels((folder / f"{name}.bch").read_bytes()))
        except Exception:
            continue
        index[name] = len(tiles) - 1
    rows = (len(tiles) + ICONS_PER_ROW - 1) // ICONS_PER_ROW
    sheet = np.zeros((rows * ICON, ICONS_PER_ROW * ICON, 4), np.uint8)
    for i, t in enumerate(tiles):
        y, x = divmod(i, ICONS_PER_ROW)
        sheet[y * ICON:(y + 1) * ICON, x * ICON:(x + 1) * ICON] = t
    Image.fromarray(sheet, "RGBA").save(out, optimize=True)
    return index


def items(syscmn: Pack, db: sqlite3.Connection) -> list[dict]:
    d = syscmn.get("item.bxon").data
    en = {loc: tr for loc, tr in db.execute(
        "select e.loc, s.translation from entry e join source s on s.id = e.source_id "
        "where e.file = '/syscmn.pack' and e.entry = 'item.bxon'")}
    n, p = _root(d)
    out = []
    for i in range(n):
        o = p + i * ITEM_SIZE
        loc, jp = _string_at(d, o)
        name = _clean(en.get(loc) or jp)
        plus = d[o + 0x10]
        out.append({
            "id": i,
            "name": name,
            "plus": plus,                       # forge level; +1..+3 follow their base item
            "icon": _ascii_at(d, o + 8),
            "type": d[o + 0x0C],
            "slot": d[o + 0x29],
            "attack": struct.unpack_from("<H", d, o + 0x2A)[0] if d[o + 0x29] < 10 else 0,
            "defence": struct.unpack_from("<H", d, o + 0x2C)[0] if 10 <= d[o + 0x29] < 14 else 0,
            "buy": _u32(d, o + 0x18),
            "sell": _u32(d, o + 0x1C),
        })
    return out


def level_tables(syscmn: Pack) -> dict[str, list[list[int]]]:
    out = {}
    for name in sorted(set(RECORD_PARAMS)):
        d = syscmn.get(f"player_param_{name}.bxon").data
        n, p = _root(d)
        rows = []
        for i in range(n):
            r = struct.unpack_from("<12H", d, p + i * LEVEL_ROW)
            # exp (u32), max HP, max MP, strength, resilience, agility, deftness, charm,
            # magical might, magical mending, total skill points
            rows.append([r[0] | r[1] << 16, *r[2:]])
        out[name] = rows
    return out


def _strings(db: sqlite3.Connection, pack: str, entry: str) -> dict[int, str]:
    return {loc: tr or plain for loc, plain, tr in db.execute(
        "select e.loc, s.plain, s.translation from entry e join source s on s.id = e.source_id "
        "where e.file = ? and e.entry = ?", (pack, entry))}


def _pointer_list(d: bytes, names: dict[int, str]) -> list[str]:
    n, p = _root(d)
    out = []
    for i in range(n):
        loc, jp = _string_at(d, p + 4 * i)
        out.append(_clean(names.get(loc) or jp))
    return out


SPELL_BASE = 332        # battle_act_text: index of Frizz, the first spell name
SKILL_COUNT = 171       # battle_act_text: ability names before the descriptions


def panels(syscmn: Pack, gamecmn: Pack, db: sqlite3.Connection) -> list[dict]:
    """Skill panels (panel.bxon, 12 bytes each): kind 0 = ability, 1 = spell, 2 = stat bonus.

    Checked in the emulator: the hero's panel 25 (ability 0) is Flame Slash, Erik's panel 77 (ability 24)
    is Ooze Bruiser; the ability ids skip the "(Unused)" names in the action text list.
    """
    acts = _pointer_list(gamecmn.get("battle_act_text.bxon").data,
                         _strings(db, "/gamecmn.pack", "battle_act_text.bxon"))
    abilities = [a for a in acts[:SKILL_COUNT] if "(Unused)" not in a]
    ps = gamecmn.get("passive_skill.bxon").data
    ps_names = _strings(db, "/gamecmn.pack", "passive_skill.bxon")
    pn, pp = _root(ps)
    passives = []
    for i in range(pn):
        o = pp + 16 * i
        loc, jp = _string_at(ps, o)
        text = _clean(ps_names.get(loc) or jp)
        value = struct.unpack_from("<H", ps, o + 10)[0]
        weapon = ps[o + 9]
        text = text.replace("{v:8}", str(value))
        if weapon < len(WEAPON_KINDS):
            text = text.replace("{v:5}", WEAPON_KINDS[weapon])
        passives.append(text)
    d = syscmn.get("panel.bxon").data
    n, p = _root(d)
    out = []
    for i in range(n):
        o = p + 12 * i
        kind = d[o]
        if kind == 0:
            sid = d[o + 1]
            name = abilities[sid] if sid < len(abilities) else f"Ability {sid}"
        elif kind == 1:
            sid = d[o + 2]
            name = acts[SPELL_BASE + sid] if SPELL_BASE + sid < len(acts) else f"Spell {sid}"
        else:
            sid = struct.unpack_from("<H", d, o + 4)[0]
            name = passives[sid] if sid < len(passives) else f"Bonus {sid}"
        out.append({"id": i, "kind": kind, "name": name, "cost": d[o + 6]})
    return out


def enemies(gamecmn: Pack, db: sqlite3.Connection) -> list[str]:
    """enemy_base.bxon names (44-byte entries). The save keeps one defeat counter per entry."""
    d = gamecmn.get("enemy_base.bxon").data
    names = _strings(db, "/gamecmn.pack", "enemy_base.bxon")
    n, p = _root(d)
    out = []
    for i in range(n):
        loc, jp = _string_at(d, p + 44 * i)
        out.append(_clean(names.get(loc) or jp).replace("{noun}", ""))
    return out


def accolades(gamecmn: Pack, db: sqlite3.Connection) -> list[dict]:
    """achievement.bxon (24-byte entries: +8 name, +16 description). The save has one bit per entry."""
    d = gamecmn.get("achievement.bxon").data
    names = _strings(db, "/gamecmn.pack", "achievement.bxon")
    n, p = _root(d)
    out = []
    for i in range(n):
        o = p + 24 * i
        loc, jp = _string_at(d, o + 8)
        dloc, djp = _string_at(d, o + 16)
        out.append({"name": _clean(names.get(loc) or jp), "text": _clean(names.get(dloc) or djp)})
    return out


WEAPON_KINDS = ["swords", "knives", "axes", "wands", "boomerangs", "greatswords", "claws", "spears",
                "whips", "staves"]


def _names(db: sqlite3.Connection, pack: Pack, packname: str, entry: str, stride: int,
           field: int) -> list[str]:
    """One name per table entry, read from the string pointer at +field."""
    d = pack.get(entry).data
    names = _strings(db, packname, entry)
    n, p = _root(d)
    out = []
    for i in range(n):
        loc, jp = _string_at(d, p + stride * i + field)
        out.append(_clean(names.get(loc) or jp))
    return out


def _column(pack: Pack, entry: str, stride: int, field: int) -> bytes:
    """Byte +field of every table entry (quest_param +6: story part 1-3, 0 = Tickington)."""
    d = pack.get(entry).data
    n, p = _root(d)
    return bytes(d[p + stride * i + field] for i in range(n))


def flag_notes(workspace: Path, syscmn: Pack, db: sqlite3.Connection) -> dict:
    from . import scriptflags
    cache: dict[str, dict[int, str]] = {}

    def english(entry: str, d: bytes, field: int) -> str:
        if entry not in cache:
            cache[entry] = _strings(db, "/syscmn.pack", entry)
        loc, jp = _string_at(d, field)
        return _clean(cache[entry].get(loc) or jp)

    found = scriptflags.notes(workspace, syscmn, english)
    sizes = {"global": 686, "record": 96, "cutscene": 84 * 8, "counter": 51}
    return {f"{table}Notes": [list(found.get(table, {}).get(i, ("", ""))) for i in range(n)]
            for table, n in sizes.items()}


def party_names(db: sqlite3.Connection) -> dict:
    """The name each character record starts with (player_initialize.bxon, record order) and
    namefix's codes: a new game stores a long name as U+E010 + k, nameCodes[k] (empty: unused)."""
    from . import namefix
    rows = db.execute("SELECT s.text, COALESCE(NULLIF(e.override, ''), NULLIF(s.translation, ''), s.text)"
                      " FROM entry e JOIN source s ON s.id = e.source_id WHERE e.entry = ? ORDER BY e.seq",
                      (namefix.INIT_TABLE,)).fetchall()
    codes = namefix.project_codes(db)
    by_code = {ord(c) - namefix.CODE_BASE: n for n, c in codes.items()}
    return {"partyNames": [en for _, en in rows],
            "nameCodes": [by_code.get(k, "") for k in range(max(by_code, default=-1) + 1)]}


def build(workspace: Path) -> dict:
    syscmn = Pack.parse((workspace / "extracted/romfs/syscmn.pack").read_bytes())
    gamecmn = Pack.parse((workspace / "extracted/romfs/gamecmn.pack").read_bytes())
    db = sqlite3.connect(workspace / "project.db")
    return {
        "items": items(syscmn, db),
        "levels": level_tables(syscmn),
        "recordParams": RECORD_PARAMS,
        **party_names(db),
        "panels": panels(syscmn, gamecmn, db),
        "enemies": enemies(gamecmn, db),
        "accolades": accolades(gamecmn, db),
        # story chapters (save 0x6B2D, 20 bytes each), side quests (state byte at 0x6F65),
        # Zoom destinations (bit mask 0x78E0) and tutorials (byte each at 0x9562)
        "scenarios": _names(db, syscmn, "/syscmn.pack", "scenario_id_list.bxon", 12, 4),
        "quests": _names(db, gamecmn, "/gamecmn.pack", "quest_param.bxon", 36, 32),
        "questParts": list(_column(gamecmn, "quest_param.bxon", 36, 6)),
        "zoom": _names(db, gamecmn, "/gamecmn.pack", "rura_point.bxon", 12, 0),
        "tutorials": _names(db, gamecmn, "/gamecmn.pack", "tutorial.bxon", 20, 4),
        # names for the global flags, record flags, cutscene flags and record counters, from the
        # event scripts that set and check them (see scriptflags.py)
        **flag_notes(workspace, syscmn, db),
    }


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        print(__doc__)
        sys.exit(1)
    data = build(Path(argv[0]))
    # item icons: icons.png next to the JSON, each item gets its tile index (-1 = none)
    sheet = Path(argv[1]).with_name("icons.png")
    index = icon_sheet(Path(argv[0]), [it["icon"] for it in data["items"]], sheet)
    for it in data["items"]:
        it["icon"] = index.get(it["icon"], -1)
    data["iconSize"], data["iconsPerRow"] = ICON, ICONS_PER_ROW
    Path(argv[1]).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {argv[1]}: {len(data['items'])} items, {len(data['levels'])} level tables")


if __name__ == "__main__":
    main(sys.argv[1:])
