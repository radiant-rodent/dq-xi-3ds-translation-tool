"""Party names longer than the save's name field (6 characters).

Every party member's name is copied from syscmn.pack:player_initialize.bxon into the save at a
new game; the save keeps 6 UTF-16 units and a NUL per name (member record +2, 14 bytes), so
"Veronica" / "Sylvando" were cut ("Veroni", "Sylv.") and "Hendrik" (7) ran into the next field
("Hendrik$"). Only the hero can be renamed in the game.

The exporter writes such a name into player_initialize as one private-use character
(CODE_BASE + k) and the name itself into a table in the executable; the grammar stub's
pane_fix calls `names` before the grammar pass, which replaces each code by its name in the
pane text (every name the game shows goes through a pane's SetString, like the grammar
markers). Saves started before keep their stored short names.

    names(r0 = UTF-16 text, NUL-terminated, r1 = capacity in units including the NUL)
    table at TABLE: the names in ASCII, each NUL-terminated, in code order

Assembled with keystone (`python -m dq11tool.namefix --write` regenerates CODE); the code is
fixed, the exporter only writes the table.
"""
from __future__ import annotations

BASE = 0x100000
CAVE = 0x724200            # free .text tail between autofit (0x724040-0x7241FC) and grammar
TABLE = 0x724320
TABLE_END = 0x724380       # grammar_code.CAVE (the table needs ~55 bytes; the grammar code outgrew 0x724A00-0x724400)
CODE_BASE = 0xE010
MAX_NAMES = 16
MAX_UNITS = 6              # the save's name field without its NUL
MAX_NAME = 15              # grammar_asm's sstub grows a pane by 14 units per name code ("Hooded Stranger")
INIT_TABLE = "player_initialize.bxon"

SOURCE = r"""
names:
    push {r4-r8, lr}
    mov r4, r0
    sub r5, r1, #1              @ max length (units, without the NUL)
    mov r6, #0
nm_len:
    add r0, r4, r6, lsl #1
    ldrh r0, [r0]
    cmp r0, #0
    addne r6, r6, #1
    bne nm_len
    mov r7, #0                  @ i
nm_scan:
    cmp r7, r6
    bge nm_out
    add r0, r4, r7, lsl #1
    ldrh r1, [r0]
    sub r1, r1, #0xE000
    subs r1, r1, #0x10          @ code index
    blo nm_next
    cmp r1, #15
    bhi nm_next
    ldr r2, nm_table
nm_find:
    cmp r1, #0
    beq nm_got
nm_skip:
    ldrb r3, [r2], #1
    cmp r3, #0
    bne nm_skip
    sub r1, r1, #1
    b nm_find
nm_got:
    mov r3, #0                  @ name length
nm_nl:
    ldrb r0, [r2, r3]
    cmp r0, #0
    addne r3, r3, #1
    bne nm_nl
    cmp r3, #0
    beq nm_next                 @ no name for this code: left as it is
    sub r8, r3, #1              @ growth
    add ip, r6, r8              @ new length, at most the capacity
    cmp ip, r5
    movgt ip, r5
    sub r0, ip, #1              @ move the tail: j = new length - 1 down to i + name length
    add r1, r7, r3
nm_mv:
    cmp r0, r1
    blt nm_cp
    sub lr, r0, r8
    add lr, r4, lr, lsl #1
    ldrh lr, [lr]
    add r1, r4, r0, lsl #1
    strh lr, [r1]
    add r1, r7, r3
    sub r0, r0, #1
    b nm_mv
nm_cp:
    mov r0, #0
nm_cl:
    cmp r0, r3
    bge nm_end
    add r1, r7, r0
    cmp r1, ip
    bge nm_end
    ldrb lr, [r2, r0]
    add r1, r4, r1, lsl #1
    strh lr, [r1]
    add r0, r0, #1
    b nm_cl
nm_end:
    add r0, r4, ip, lsl #1
    mov r1, #0
    strh r1, [r0]
    mov r6, ip
    add r7, r7, r3
    b nm_scan
nm_next:
    add r7, r7, #1
    b nm_scan
nm_out:
    pop {r4-r8, pc}
nm_table:
    .word TABLEADDR
"""

CODE = bytes.fromhex(
    "f0412de90040a0e1015041e20060a0e3860084e0b000d0e1000050e301608612faffff1a0070a0e3060057e1380000aa"
    "870084e0b010d0e10e1a41e2101051e23100003a0f0051e32f00008ac4209fe5000051e30400000a0130d2e4000053e3"
    "fcffff1a011041e2f8ffffea0030a0e30300d2e7000050e301308312fbffff1a000053e32000000a018043e208c086e0"
    "05005ce105c0a0c101004ce2031087e0010050e1070000ba08e040e08ee084e0b0e0dee1801084e0b0e0c1e1031087e0"
    "010040e2f5ffffea0000a0e3030050e1070000aa001087e00c0051e1040000aa00e0d2e7811084e0b0e0c1e1010080e2"
    "f5ffffea8c0084e00010a0e3b010c0e10c60a0e1037087e0c6ffffea017087e2c4ffffeaf081bde820437200")


def assemble() -> bytes:
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    return bytes(ks.asm(SOURCE.replace("TABLEADDR", f"{TABLE:#x}"), CAVE)[0])


# A save stores the code, so a code must keep meaning the same character from one patch to the
# next: these are the codes of the first release (its English names in sorted order: Hendrik,
# King Irwin, Kurucchi, Stranger, Sylvando, Veronica), by Japanese name; new long names follow.
FIXED = ("グレイグ", "アーウィン", "クルッチ", "謎の青年", "シルビア", "ベロニカ")


def assign(names: list[tuple[str, str]]) -> dict[str, str]:
    """{long name: its code character} for the (Japanese, English) names that do not fit the
    save: FIXED ones keep their code, the others are numbered after them in sorted order."""
    long = {(jp, en) for jp, en in names if len(en) > MAX_UNITS and en.isascii()}
    codes = {en: chr(CODE_BASE + FIXED.index(jp)) for jp, en in long if jp in FIXED}
    k = len(FIXED)
    for en in sorted({en for _, en in long} - set(codes)):
        codes[en] = chr(CODE_BASE + k)
        k += 1
    if k > MAX_NAMES:
        raise ValueError(f"{k} party name codes > {MAX_NAMES}")
    return codes


def project_codes(db) -> dict[str, str]:
    """assign() over the names of INIT_TABLE, as the exporter sees them."""
    rows = db.execute("SELECT DISTINCT s.text, COALESCE(NULLIF(e.override, ''), s.translation) FROM entry e"
                      " JOIN source s ON s.id = e.source_id WHERE e.entry = ? AND s.translation IS NOT NULL"
                      " AND s.translation != ''", (INIT_TABLE,)).fetchall()
    return assign([(r[0], r[1]) for r in rows])


def table(codes: dict[str, str]) -> bytes:
    """The names in code order; a code without a name (a FIXED name now short) gets an empty
    entry, which `names` leaves as it is."""
    too_long = [n for n in codes if len(n) > MAX_NAME]
    if too_long:
        raise ValueError(f"party names longer than {MAX_NAME}: {too_long}")
    by_code = {ord(c) - CODE_BASE: n for n, c in codes.items()}
    data = b"".join(by_code.get(k, "").encode("ascii") + b"\0" for k in range(max(by_code, default=-1) + 1))
    if len(data) > TABLE_END - TABLE:
        raise ValueError(f"name table {len(data)} bytes > {TABLE_END - TABLE}")
    return data


def patch(code: bytes, codes: dict[str, str]) -> tuple[bytes, list[str]]:
    """code with `names` and the table (grammar_code's pane_fix calls it)."""
    out = bytearray(code)
    c, t = CAVE - BASE, TABLE - BASE
    if any(out[c:c + len(CODE)]) and out[c:c + len(CODE)] != CODE:
        return bytes(code), [f"names: code cave at {CAVE:#x} is not free"]
    data = table(codes)
    out[c:c + len(CODE)] = CODE
    out[t:TABLE_END - BASE] = data + bytes(TABLE_END - TABLE - len(data))
    return bytes(out), []


def resolve(text: str, codes: dict[str, str], cap: int | None = None) -> str:
    """Python reference of `names`."""
    by_code = {v: k for k, v in codes.items()}
    s = "".join(by_code.get(ch, ch) for ch in text)
    return s if cap is None else s[:cap - 1]


if __name__ == "__main__":
    import os
    import re
    import sys
    code = assemble()
    print(len(code), "bytes at", hex(CAVE))
    if CAVE + len(code) > TABLE:
        sys.exit("code runs into the table")
    if "--write" in sys.argv:
        p = os.path.abspath(__file__)
        src = open(p, encoding="utf-8").read()
        hexs = "\n".join(f'    "{code[i:i + 48].hex()}"' for i in range(0, len(code), 48))
        src = re.sub(r'CODE = bytes\.fromhex\(\n(?:    "[0-9a-f]*"\n?)+\)', "CODE = bytes.fromhex(\n" + hexs + ")", src)
        open(p, "w", encoding="utf-8").write(src)
