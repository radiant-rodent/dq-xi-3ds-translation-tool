"""Test builds only: scenario battles with chosen monsters (gamecmn.pack:scenario_battle.bxon).

The scripts start fixed ("scenario") battles by index: LoadScenarioBattleEncount(id)
(testroom.py). ScenarioBattleParam is an array of 64-byte records:
    +0x00 (count, relptr) monsters: 4 bytes each, u16 enemy id (enemy_base.bxon), u8 0, u8 count
    +0x08, +0x10, +0x18 (count, relptr) further arrays (left alone)
    +0x26 u16 battle zone / background (see ZONE note in docs/AGENT_GUIDE.md)
A test run rewrites one record (TEST_ENTRY): its monster array is appended to the file and
re-pointed (like ietalk.py does for talk lines), the zone is set if given.

Monsters: enemy_base.bxon, 44-byte records (enemy id = record index): +0x00 relptr name string.
"""
from __future__ import annotations

import os
import sqlite3
import struct

from .bxon import MARK

PACK = "/gamecmn.pack"
MEMBER = "scenario_battle.bxon"
ENEMIES = "enemy_base.bxon"
REC = 64
ENEMY_REC = 44
TEST_ENTRY = 25          # LoadScenarioBattleEncount(25): Heliodor arena, Skelegon + Chasmonaut
ZONE_AT = 0x26


def _array(d: bytes, at: int) -> tuple[int, int]:
    n, rp = struct.unpack_from("<II", d, at)
    return n, at + 4 + rp


def records(d: bytes) -> tuple[int, int]:
    """(count, offset of record 0)."""
    return _array(d, 0x20)


def read(d: bytes, k: int) -> dict:
    n, base = records(d)
    o = base + REC * k
    cnt, a = _array(d, o)
    mons = [struct.unpack_from("<HBB", d, a + 4 * i) for i in range(cnt)]
    return {"monsters": [(m[0], m[2]) for m in mons], "pad": [m[1] for m in mons],
            "zone": struct.unpack_from("<H", d, o + ZONE_AT)[0], "raw": d[o:o + REC]}


def write(d: bytes, k: int, monsters: list[tuple[int, int]], zone: int | None = None) -> bytes:
    """Record k with monsters [(enemy id, count)] (and battle zone)."""
    out = bytearray(d)
    n, base = records(d)
    if not 0 <= k < n:
        raise IndexError(k)
    o = base + REC * k
    while len(out) % 4:
        out += b"@"
    out += MARK
    at = len(out)
    for eid, count in monsters:
        out += struct.pack("<HBB", eid, 0, count)
    struct.pack_into("<II", out, o, len(monsters), at - (o + 4))
    if zone is not None:
        struct.pack_into("<H", out, o + ZONE_AT, zone)
    return bytes(out)


def enemy_names(enemy_base: bytes, db_path: str | None = None) -> dict[int, tuple[str, str]]:
    """{enemy id: (Japanese name, translation or '')}."""
    from .bxon import Bxon
    n, base = _array(enemy_base, 0x20)
    tr = {}
    if db_path and os.path.exists(db_path):
        db = sqlite3.connect(db_path)
        tr = dict(db.execute("SELECT e.loc, COALESCE(NULLIF(e.override, ''), s.translation) FROM entry e "
                             "JOIN source s ON s.id = e.source_id WHERE e.entry = ?", (ENEMIES,)).fetchall())
    out = {}
    for k in range(n):
        o = base + ENEMY_REC * k
        rp = struct.unpack_from("<I", enemy_base, o)[0]
        eid = k
        s = o + rp
        end = s
        while enemy_base[end:end + 2] != b"\0\0":
            end += 2
        jp = enemy_base[s:end].decode("utf-16-le", "replace")
        out.setdefault(eid, (jp, (tr.get(s) or "").replace("{noun}", "")))
    return out


def find_enemy(names: dict, query: str) -> int:
    """Enemy id by id number or (English / Japanese) name; exact match first."""
    if query.isdigit():
        return int(query)
    norm = lambda t: " ".join(t.split()).lower()        # names contain line breaks
    q = norm(query)
    for eid, (jp, en) in sorted(names.items()):
        if q in (norm(en), norm(jp)):
            return eid
    hits = [eid for eid, (jp, en) in sorted(names.items()) if q in norm(en)]
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"monster {query!r}: " + (f"ambiguous {[names[h][1] for h in hits[:8]]}" if hits else "not found"))


def parse_party(names: dict, spec: str) -> list[tuple[int, int]]:
    """"Slime x3, Dracky" -> [(id, 3), (id, 1)]."""
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        name, _, cnt = part.rpartition(" x")
        if not name or not cnt.isdigit():
            name, cnt = part, "1"
        out.append((find_enemy(names, name), int(cnt)))
    return out


def patch_pack(pack_bytes: bytes, monsters: list[tuple[int, int]], zone: int | None = None,
               entry: int = TEST_ENTRY) -> bytes:
    """gamecmn.pack with scenario battle `entry` replaced."""
    from .pack import Pack
    p = Pack.parse(pack_bytes)
    for e in p.entries:
        if e.name == MEMBER:
            e.data = write(e.data, entry, monsters, zone)
            return p.build()
    raise ValueError(f"{MEMBER} not in pack")
