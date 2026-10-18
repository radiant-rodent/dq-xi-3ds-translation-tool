"""Test builds only: any shop's goods at the test room's shopkeeper (emutest/scenario.py shop --id N).

fieldcmn.pack:shop.bxon holds 179 shops as 52-byte records from 0x2C: u8 kind, u8, then up to
23 u16 item ids (0xFFFF = none), u16. The game picks the record from the NPC that is talked to
(common.ShopEvent calls StartUI(2, -1); the second argument is not a shop index), so a test
build copies record N over every record: the scenario's StartUI(2, ...) then sells shop N's
goods, whichever record it uses.
"""
from __future__ import annotations

import struct

PACK = "/fieldcmn.pack"
MEMBER = "shop.bxon"
COUNT_AT, FIRST, SIZE = 0x20, 0x2C, 52


def records(data: bytes) -> list[bytes]:
    n = struct.unpack_from("<I", data, COUNT_AT)[0]
    return [data[FIRST + k * SIZE:FIRST + (k + 1) * SIZE] for k in range(n)]


def items(rec: bytes) -> list[int]:
    ids = struct.unpack_from("<23H", rec, 2)
    return [i for i in ids if i != 0xFFFF]


def write(data: bytes, shop: int) -> bytes:
    recs = records(data)
    if not 0 <= shop < len(recs):
        raise ValueError(f"shop {shop} not in 0..{len(recs) - 1}")
    out = bytearray(data)
    for k in range(len(recs)):
        out[FIRST + k * SIZE:FIRST + (k + 1) * SIZE] = recs[shop]
    return bytes(out)


def patch_pack(pack_bytes: bytes, shop: int) -> bytes:
    from .pack import Pack
    p = Pack.parse(pack_bytes)
    for e in p.entries:
        if e.name == MEMBER:
            e.data = write(e.data, shop)
            return p.build()
    raise ValueError(f"{MEMBER} not in pack")


def biggest(data: bytes, n: int = 10) -> list[tuple[int, int, int]]:
    """(shop, kind, item count) of the shops with the most goods."""
    rows = [(k, r[0], len(items(r))) for k, r in enumerate(records(data))]
    return sorted(rows, key=lambda x: -x[2])[:n]
