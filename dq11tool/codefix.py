"""Small fixed patches of Japanese pieces the executable glues onto names at runtime.

The battle reward message is "{v:2}{CR}<n> experience points" where the game builds {v:2}
from the hero's name and one of two particles: "たちは　それぞれ" (with the party; a normal
translatable string) or "は" right behind it (alone). That one-character string is not picked
up by the string scanner, so it is patched here, found by its exact neighbourhood in the
original executable so nothing else can match.

The battle window's monster count is the format "%d匹" (0x38B1E8, "1匹" next to "Slime");
the English game shows just the number.
"""
from __future__ import annotations

# (UTF-16 signature, [(character index inside the signature, Japanese, English)]);
# the English must not be longer than the Japanese
PATCHES = [
    ("たちは　それぞれ\0は\0", [(9, "は", "")]),
    ("%d匹\0", [(2, "匹", "")]),
    # {v:5} of the encounter lines ("The monsters appear{v:5}", 0x28B858 sets it to one of
    # these): full-width marks, drawn with a wide gap after English text
    ("%ls\0？\0！\0", [(4, "？", "?"), (6, "！", "!")]),
]


# Single instructions: (address, original bytes, new bytes, why)
INSTRUCTIONS = [
    # label format strings with a full-width colon ("%ls：", "%u：", "%d：", "："), e.g. the Skill
    # Panel's tree totals "Swordmastery：" ran out of their 96 px panes: ASCII ':' (UTF-16 words)
    (0x258C88, "1aff0000", "3a000000", "'：' -> ':'"),
    (0x3AA178, "73001aff", "73003a00", "'%ls：' -> '%ls:'"),
    (0x3F21E8, "73001aff", "73003a00", "'%ls：' -> '%ls:'"),
    (0x453278, "73001aff", "73003a00", "'%ls：' -> '%ls:'"),
    (0x45E0EC, "1aff0000", "3a000000", "'%u：' -> '%u:'"),
    (0x4CFB44, "1aff0000", "3a000000", "'%d：' -> '%d:'"),
    # one-line copy of a multi-line text (item descriptions in the 2D bag / battle item list,
    # 0x66C8B0: each "\n" becomes r3): a full-width space ("Simple sword　loved in　Cobblestone.",
    # a 2D9 gap of 11 px) -> an ASCII space
    (0x66C870, "033aa0e3", "2030a0e3", "mov r3, #0x3000 -> mov r3, #0x20"),
    # Save Code name encoder (0x5F00B8): a name character outside U+3000-30FE, "…" and the
    # full-width forms fails before the table lookup; the lookup itself fails for anything
    # not in NAME_TABLE, so the range check goes (Latin names, see patch_name_table)
    (0x5F0108, "2200003a", "00f020e3", "blo fail -> nop"),
    # Save Code entry check (0x5EF474): every character must be a hiragana U+3042-308F
    # before it is looked up in the alphabet; the lookup alone decides (SAVE_CODE_ALPHABET)
    (0x5EF4A4, "0020a033", "0020a0e3", "movlo r2, #0 -> mov r2, #0"),
    (0x5EF4A8, "0c00002a", "00f020e3", "bhs fail -> nop"),
    # Key types of the name keyboard, by key index (namekbd: column * 6 + row): 60 and 61
    # (column 10, rows 1-2) are the ゜/゛ marks that voice the previous kana, so the Latin
    # keys there ('0' and '2', on page 2 accents) typed nothing. Name entry, page 1
    # (0x60EF84) and page 2 (0x60EFF8): jump table entries -> the plain key case
    (0x60EFA4, "bcef6000", "e0ef6000", "page 1 key 60: handakuten -> plain"),
    (0x60EFA8, "c4ef6000", "e0ef6000", "page 1 key 61: dakuten -> plain"),
    (0x60F018, "30f06000", "54f06000", "page 2 key 60: handakuten -> plain"),
    (0x60F01C, "38f06000", "54f06000", "page 2 key 61: dakuten -> plain"),
    # Save Code entry (0x60F06C): also key 35 (row 6, column 6: the full-width space,
    # ignored there; 'z' now)
    (0x60F090, "0720a003", "0020a003", "code key 35: space -> plain"),
    (0x60F09C, "0220a003", "0020a003", "code key 60: handakuten -> plain"),
    (0x60F0A8, "0120a003", "0020a003", "code key 61: dakuten -> plain"),
]


# The Save Code itself: 17 characters of a 64-character alphabet (6 bits each), shown by the
# priest (0x5EF33C: bits -> character) and read back on the title screen (0x5EF3CC); the
# Japanese one is 64 hiragana, typed on the hiragana page of the name keyboard. The English
# one is typed on the Latin page (namekbd.PAGE_LATIN, which has all of these). Position i of
# the Japanese alphabet becomes position i here, so a Japanese code converts letter by letter.
SAVE_CODE_ALPHABET_AT = 0x72EF3C
SAVE_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?"
SAVE_CODE_ALPHABET_JP = ("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ"
                         "がぎぐげござじずぜぞだぢづでどばびぶべぼ")

# Strings the priest's Save Code message (0x3954C8) is built from, inline after the code:
# (address, Japanese, English); the English is padded with NULs to the Japanese length
SAVE_CODE_STRINGS = [
    (0x395BB4, "「", ""),             # speech mark before the code (English lines have none)
    # after the 3rd group: line 2 was indented under the 「; the builder counts this one as
    # 2 characters (a 1-character one lost the next separator), so a space stays before the
    # line break
    (0x395BAC, "\n　", " \n"),
    # Not Save Code: the message window text builder (0x614CF0: speaker prefix, then this
    # when the message's flag +0x15 is set, then the text) - the Japanese opening speech
    # mark in front of English lines (user report 2026-10-14)
    (0x615044, "「", ""),
]


def patch_save_code(code: bytes, original: bytes) -> tuple[bytes, list[str]]:
    out, problems = bytearray(code), []
    o = SAVE_CODE_ALPHABET_AT - 0x100000
    n = 2 * len(SAVE_CODE_ALPHABET_JP)
    if original[o:o + n + 2] != SAVE_CODE_ALPHABET_JP.encode("utf-16-le") + bytes(2):
        problems.append(f"save code alphabet at {SAVE_CODE_ALPHABET_AT:#x}: not as expected")
    else:
        assert len(SAVE_CODE_ALPHABET) == len(set(SAVE_CODE_ALPHABET)) == 64
        out[o:o + n] = SAVE_CODE_ALPHABET.encode("utf-16-le")
    for at, jp, en in SAVE_CODE_STRINGS:
        o = at - 0x100000
        raw = (jp + "\0").encode("utf-16-le")
        if original[o:o + len(raw)] != raw:
            problems.append(f"save code text {jp!r} at {at:#x}: not as expected")
            continue
        out[o:o + len(raw)] = (en + "\0" * (len(jp) + 1 - len(en))).encode("utf-16-le")
    return bytes(out), problems


# The Save Code ("ふっかつのじゅもん", priest) stores the hero's name as indices into a table
# of 256 name characters (0x72EFBE, read by 0x5F00B0 / 0x5F01A8; entry 0 = end): kana, digits,
# a few symbols, unused entries "␣" (U+2423). A name with a character not in the table gives
# no code at all (the priest showed only "「", user report 2026-10-14) - every name typed on
# the Latin keyboard (namekbd). The keyboard's characters go into the unused entries, then
# into the katakana entries (no longer typeable); hiragana names keep their codes.
NAME_TABLE = 0x72EFBE
NAME_TABLE_LEN = 256
NAME_TABLE_FREE = "␣"


def name_table_chars() -> str:
    from . import namekbd
    out = []
    for ch in "".join(namekbd.PAGE_LATIN) + namekbd.ACCENT_UPPER + namekbd.ACCENT_LOWER + namekbd.FILLERS:
        if ch not in out and ch != namekbd.SPACE:
            out.append(ch)
    return "".join(out)


def patch_name_table(code: bytes, original: bytes) -> tuple[bytes, list[str]]:
    o = NAME_TABLE - 0x100000
    raw = original[o:o + 2 * NAME_TABLE_LEN]
    table = list(raw.decode("utf-16-le"))
    latin = [ch for ch in name_table_chars() if ch not in table]
    free = [i for i, ch in enumerate(table) if ch == NAME_TABLE_FREE]
    free += [i for i, ch in enumerate(table) if "ァ" <= ch <= "ヺ"]
    if table[0] != "\0" or "あ" not in table or len(free) < len(latin):
        return code, [f"save code name table at {NAME_TABLE:#x}: not as expected "
                      f"({len(latin)} characters, {len(free)} free entries)"]
    for i, ch in zip(free, latin):
        table[i] = ch
    out = bytearray(code)
    out[o:o + 2 * NAME_TABLE_LEN] = "".join(table).encode("utf-16-le")
    return bytes(out), []


def patch_instructions(code: bytes) -> tuple[bytes, list[str]]:
    out, problems = bytearray(code), []
    for at, orig, new, why in INSTRUCTIONS:
        o = at - 0x100000
        cur = bytes(out[o:o + 4])
        if cur == bytes.fromhex(new):
            continue
        if cur != bytes.fromhex(orig):
            problems.append(f"instruction at {at:#x} ({why}): unexpected {cur.hex()}")
            continue
        out[o:o + 4] = bytes.fromhex(new)
    return bytes(out), problems


# Formats that need more room than their slot: moved by codestr.Mover (the export's code
# moves), every reference re-pointed. "%ls%c" builds the battle name of a monster with its
# letter ('A' + index: 0x2699B0 target label, 0x5E5BF4 battle messages); the user saw "SlimeA".
FORMAT_MOVES = [("%ls%c", "%ls %c", 2)]       # (original, new, expected count)


def format_moves(original: bytes) -> tuple[dict[int, tuple[str, int]], list[str]]:
    """{file offset: (new raw string, slot capacity)} for FORMAT_MOVES, found in the original
    executable (NUL-terminated, even offsets; inline in .text after code, so the word before
    is an instruction or a pointer, not a NUL)."""
    out, problems = {}, []
    for old, new, count in FORMAT_MOVES:
        b = old.encode("utf-16-le") + b"\0\0"
        hits, at = [], original.find(b)
        while at >= 0:
            if at % 2 == 0:
                hits.append(at)
            at = original.find(b, at + 1)
        if len(hits) != count:
            problems.append(f"executable format {old!r}: {len(hits)} found, {count} expected")
            continue
        for at in hits:
            out[at] = (new, len(b))
    return out, problems


def patch(code: bytes, original: bytes | None = None) -> tuple[bytes, list[str]]:
    """Returns (code, problems). The places are looked up in `original` (the unpatched
    executable), since translated strings around them may already be replaced in `code`."""
    original = original or code
    out = bytearray(code)
    problems = []
    for sig, pieces in PATCHES:
        b = sig.encode("utf-16-le")
        at = original.find(b)
        while at >= 0 and at % 2:
            at = original.find(b, at + 1)
        if at < 0 or original.find(b, at + 2) >= 0:
            problems.append(f"executable text {sig.split(chr(0))[0]!r} not found (or not unique)")
            continue
        for i, jp, en in pieces:
            assert sig[i:i + len(jp)] == jp and len(en) <= len(jp)
            o = at + 2 * i
            out[o:o + 2 * len(jp)] = (en + "\0" * (len(jp) - len(en))).encode("utf-16-le")
    return bytes(out), problems
