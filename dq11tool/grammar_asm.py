"""ARM source of the grammar pass (grammar.resolve in ARM), assembled with keystone.

    gfix(r0 = UTF-16 string, NUL-terminated, r1 = capacity in units including the NUL)

The string is edited in place; every register except r0-r3/ip is preserved (AAPCS). Kept in
the tool as source so it can be reassembled (`python -m dq11tool.grammar_asm`); the patch
uses the bytes in grammar_code.py.
"""
from __future__ import annotations

SOURCE = r"""
gfix:
    push {r4-r11, lr}
    mov r4, r0                  @ buf
    sub r5, r1, #1              @ max length (units, without the NUL)
    sub sp, sp, #8              @ [sp]: 1 once a {grp} was seen (the text is plural from there)
    mov r0, #0
    str r0, [sp]
    mov r6, #0                  @ len
glen:
    add r0, r4, r6, lsl #1
    ldrh r0, [r0]
    cmp r0, #0
    addne r6, r6, #1
    bne glen
    mov r7, #0                  @ i
    mov r8, #0xE000             @ marker base
loop:
    cmp r7, r6
    bge done
    add r0, r4, r7, lsl #1
    ldrh r9, [r0]
    sub r10, r9, r8             @ marker index
    cmp r10, #10
    bhi next
    cmp r10, #3
    bls article
    cmp r10, #4
    beq plural
    cmp r10, #5
    beq number
    cmp r10, #8
    beq noun
    cmp r10, #9
    beq group
    cmp r10, #10
    beq number                  @ {s:one|more}: same block, chosen by the plural flag
next:
    add r7, r7, #1
    b loop

@ ---- {the} {The} {a} {A}
article:
    add r0, r7, #1
    cmp r0, r6
    bge drop1
    add r0, r4, r0, lsl #1
    ldrh r0, [r0]
    add r1, r8, #8
    cmp r0, r1
    bne drop1
    mov r11, #0                 @ +8: "an " / "An " instead of "a " / "A "
    cmp r10, #2
    blo art_put
    add r0, r7, #2
    cmp r0, r6
    bge art_put
    add r0, r4, r0, lsl #1
    ldrh r0, [r0]
    bl is_vowel
    cmp r0, #0
    movne r11, #8
art_put:
    adr r2, s_the
    add r2, r2, r10, lsl #2     @ 4 bytes per article string
    add r2, r2, r11
    mov r3, #4
    cmp r10, #2
    movhs r3, #2
    cmp r11, #0
    movne r3, #3
    mov r11, r3
    mov r0, r7
    mov r1, #2
    bl replace
    add r7, r7, r11
    bl lower_at_i
    b loop

drop1:
    mov r0, r7
    mov r1, #1
    mov r3, #0
    bl replace
    b loop

@ ---- {noun} outside an article: removed, lower case mid-sentence
noun:
    mov r0, r7
    mov r1, #1
    mov r3, #0
    bl replace
    mov r0, r7
    sub r0, r0, #1
sp_back:
    cmp r0, #0
    blt loop                    @ start of the message: keep the capital
    add r1, r4, r0, lsl #1
    ldrh r1, [r1]
    cmp r1, #0x20
    subeq r0, r0, #1
    beq sp_back
    adr r2, s_end
sent:
    ldrb r3, [r2], #1
    cmp r3, #0
    beq not_sent
    cmp r3, r1
    bne sent
    b loop                      @ after . ! ? or a line break: keep the capital
not_sent:
    bl lower_at_i
    b loop

@ ---- {grp}: the name before it is a group (plural)
group:
    mov r0, #1
    str r0, [sp]
    b drop1

@ ---- {pl}
plural:
    mov r0, #0                  @ p1
    mov r1, #0                  @ p2
    cmp r7, #1
    blt pl_s
    add r2, r4, r7, lsl #1
    ldrh r0, [r2, #-2]
    cmp r7, #2
    ldrhge r1, [r2, #-4]
    orr r2, r0, #0x20           @ lower case p1
    cmp r2, #0x73               @ s
    cmpne r2, #0x78             @ x
    cmpne r2, #0x7A             @ z
    beq pl_es
    cmp r2, #0x68               @ h after c / s
    bne pl_y
    orr r3, r1, #0x20
    cmp r3, #0x63
    cmpne r3, #0x73
    beq pl_es
pl_y:
    cmp r2, #0x79               @ y after a consonant
    bne pl_s
    cmp r1, #0
    beq pl_s
    mov r0, r1
    bl is_vowel
    cmp r0, #0
    bne pl_s
    sub r0, r7, #1
    mov r1, #2
    adr r2, s_ies
    mov r3, #3
    bl replace
    add r7, r7, #2
    b loop
pl_es:
    mov r0, r7
    mov r1, #1
    adr r2, s_es
    mov r3, #2
    bl replace
    add r7, r7, #2
    b loop
pl_s:
    mov r0, r7
    mov r1, #1
    adr r2, s_es
    add r2, r2, #1
    mov r3, #1
    bl replace
    add r7, r7, #1
    b loop

@ ---- {n:one|more}
number:
    mov r11, r10                @ 5 {n:  10 {s:
    add r0, r8, #6
    mov r1, r7
    bl find
    mov r9, r0                  @ mid
    cmp r9, #0
    blt drop1
    add r0, r8, #7
    mov r1, r9
    bl find
    mov r10, r0                 @ end
    cmp r10, #0
    blt drop1
    cmp r11, #10
    bne n_by_number
    ldr r0, [sp]                @ {s:: 'one' unless a group was named
    eor r0, r0, #1
    b n_chosen
n_by_number:
    bl number_is_one
n_chosen:
    cmp r0, #0
    beq n_more
    mov r0, r9                  @ one: drop "|more}" then the start marker
    sub r1, r10, r9
    add r1, r1, #1
    mov r3, #0
    bl replace
    b drop1
n_more:
    mov r0, r10                 @ more: drop the end marker, then "{n:one|"
    mov r1, #1
    mov r3, #0
    bl replace
    mov r0, r7
    sub r1, r9, r7
    add r1, r1, #1
    mov r3, #0
    bl replace
    b loop

done:
    add sp, sp, #8
    pop {r4-r11, pc}

@ ---- helpers (r4 buf, r5 max, r6 len, r7 i)
@ find(r0 = char, r1 = from) -> index or -1
find:
    cmp r1, r6
    movge r0, #-1
    bxge lr
    add r2, r4, r1, lsl #1
    ldrh r2, [r2]
    cmp r2, r0
    moveq r0, r1
    bxeq lr
    add r1, r1, #1
    b find

@ is_vowel(r0) -> r0 = 1 / 0
is_vowel:
    orr r0, r0, #0x20
    adr r2, s_vow
iv_l:
    ldrb r3, [r2], #1
    cmp r3, #0
    moveq r0, #0
    bxeq lr
    cmp r3, r0
    bne iv_l
    mov r0, #1
    bx lr

@ lower_at_i: buf[i] A-Z -> a-z
lower_at_i:
    cmp r7, r6
    bxge lr
    add r0, r4, r7, lsl #1
    ldrh r1, [r0]
    sub r2, r1, #0x41
    cmp r2, #25
    addls r1, r1, #0x20
    strhls r1, [r0]
    bx lr

is_digit_at:                    @ r0 = index -> r0 = 1 / 0 (r1 clobbered)
    cmp r0, #0
    blt id_no
    cmp r0, r6
    bge id_no
    add r1, r4, r0, lsl #1
    ldrh r1, [r1]
    sub r1, r1, #0x30
    cmp r1, #9
    movls r0, #1
    bxls lr
id_no:
    mov r0, #0
    bx lr

@ number_is_one -> r0 = 1 when the nearest digit run (before i, else after it) is exactly "1"
number_is_one:
    push {r9, r10, lr}
    sub r9, r7, #1
ni_back:
    cmp r9, #0
    blt ni_fwd0
    mov r0, r9
    bl is_digit_at
    cmp r0, #0
    subeq r9, r9, #1
    beq ni_back
    sub r0, r9, #1              @ run ends at r9: one digit, and it is '1'
    bl is_digit_at
    cmp r0, #0
    bne ni_no
    b ni_check
ni_fwd0:
    mov r9, r7
ni_fwd:
    cmp r9, r6
    bge ni_no
    mov r0, r9
    bl is_digit_at
    cmp r0, #0
    addeq r9, r9, #1
    beq ni_fwd
    add r0, r9, #1
    bl is_digit_at
    cmp r0, #0
    bne ni_no
ni_check:
    add r0, r4, r9, lsl #1
    ldrh r0, [r0]
    cmp r0, #0x31
    moveq r0, #1
    movne r0, #0
    pop {r9, r10, pc}
ni_no:
    mov r0, #0
    pop {r9, r10, pc}

@ replace(r0 = at, r1 = units removed, r2 = ASCII bytes inserted, r3 = count)
replace:
    push {r4-r11, lr}
    mov r9, r0                  @ at
    sub r10, r3, r1             @ delta
    add r12, r6, r10            @ new length
    cmp r12, r5
    movgt r12, r5
    cmp r10, #0
    beq rp_copy
    blt rp_fwd
    sub r0, r12, #1             @ grow: j from newlen-1 down to at+count
    add r1, r9, r3
rp_back:
    cmp r0, r1
    blt rp_copy
    sub lr, r0, r10
    add lr, r4, lr, lsl #1
    ldrh lr, [lr]
    add r11, r4, r0, lsl #1
    strh lr, [r11]
    sub r0, r0, #1
    b rp_back
rp_fwd:
    add r0, r9, r3              @ shrink: j from at+count up to newlen-1
rp_fl:
    cmp r0, r12
    bge rp_copy
    sub lr, r0, r10
    add lr, r4, lr, lsl #1
    ldrh lr, [lr]
    add r11, r4, r0, lsl #1
    strh lr, [r11]
    add r0, r0, #1
    b rp_fl
rp_copy:
    mov r0, #0
rp_cl:
    cmp r0, r3
    bge rp_end
    add r1, r9, r0
    cmp r1, r12
    bge rp_end
    ldrb lr, [r2, r0]
    add r1, r4, r1, lsl #1
    strh lr, [r1]
    add r0, r0, #1
    b rp_cl
rp_end:
    add r0, r4, r12, lsl #1
    mov r1, #0
    strh r1, [r0]
    pop {r4-r11, lr}
    mov r6, r12
    bx lr

"""
# data after the code: 4 bytes per article ("the ", "The ", "a \0\0", "A \0\0", "an ", "An "),
# plural endings, vowels, sentence ends
DATA = (b"the The a\x20\x00\x00A\x20\x00\x00an\x20\x00An\x20\x00"
        b"es\x00\x00ies\x00aeiou\x00\x00\x00.!?\n\r\x00\x00\x00")
LABELS = {"s_the": 0, "s_es": 24, "s_ies": 28, "s_vow": 32, "s_end": 40}


def assemble(base: int) -> bytes:
    import keystone
    ends = sorted(LABELS.values()) + [len(DATA)]
    tail = ""
    for k, v in sorted(LABELS.items(), key=lambda kv: kv[1]):
        chunk = DATA[v:ends[ends.index(v) + 1]]
        tail += k + ":\n    .byte " + ", ".join(str(x) for x in chunk) + "\n"
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    enc, _ = ks.asm(SOURCE + tail, base)
    return bytes(enc)




# Hooks (2026-10-14; the scenario test log showed blank top-screen battle lines with the first
# version, which resolved every SetString):
# - 0x3A7A2C sets a pane's text from a template: 0x3A7594 walks it and composes the pane piece
#   by piece (SetString of the literal text up to a variable at the pane index so far, then the
#   variable's text appended, ...). Resolving a piece ("{The}" alone -> "") breaks the indices of
#   the pieces that follow, so pieces are left alone and the composed text is resolved once,
#   right after the real pass (0x3A7AB4, r4 = pane: `ldrb r0, [r4, #0x44]` -> `bl estub`).
# - Its measuring pass sizes the pane buffer (vt+0x58 -> 0x53F258, reallocates only when
#   length + 1 > capacity): 0x3A7A90 `uxth r1, r0` -> `bl sstub` adds 2 units per article /
#   plural marker in the template (r6 = template, r8 = its length), so "the " fits, plus 8
#   units per {v:N} tag (0x0004) or literal party name code (0xE010..0xE01F) that namefix
#   expands after the text is built (not unconditionally on every pane: +16 chars * 312 B =
#   5 KB per pane exhausted the UI layout heap when buying items in the Cobblestone shop).
# - Both pane SetString functions (0x53F940: NUL-terminated text, 0x53F9EC: text with a length)
#   end with the text and its NUL in the pane's buffer ([pane + 0xA4], capacity [+0xCC] units,
#   length [+0xCE]; r4 = pane): `ldrb r0, [r4, #0xd0]` -> `bl stub`, which resolves texts set
#   directly (names, labels) unless the caller (saved lr) is the template walker 0x3A7594.
# pane_fix (r4 = pane) holds the only `bl gfix` (textlog.patch redirects it to its logger) and
# stores the new length; before it, `bl names` (namefix.py, installed with this patch) puts the
# long party names in. gfix is idempotent: text set again every frame costs one scan.
HOOKS = ((0x53F9D8, "d000d4e5", "stub"), (0x53FA74, "d000d4e5", "stub"),
         (0x3A7AB4, "4400d4e5", "estub"), (0x3A7A90, "7010ffe6", "sstub"))
WALKER, WALKER_END = 0x3A7594, 0x3A7A2C
CAVE = 0x724400
STUB = r"""
stub:
    push {r0-r3, ip, lr}
    ldr r0, [sp, #44]           @ SetString's caller (6 registers pushed there, 6 here)
    ldr r1, walker
    sub r0, r0, r1
    ldr r1, walker_len
    cmp r0, r1
    blo stub_out                @ a piece of a template: resolved once it is complete (estub)
    bl pane_fix
    subs r6, r0, r5             @ the function returns the units it wrote (r6): the new count
    movlo r6, #0
stub_out:
    pop {r0-r3, ip, lr}
    ldrb r0, [r4, #0xd0]        @ the replaced instruction
    bx lr

estub:
    push {r0-r3, ip, lr}
    bl pane_fix
    pop {r0-r3, ip, lr}
    ldrb r0, [r4, #0x44]        @ the replaced instruction
    bx lr

sstub:
    push {r2, r3, lr}
    mov r1, #0
    mov r2, #0
ss_loop:
    cmp r2, r8
    bhs ss_done
    add r3, r6, r2, lsl #1
    ldrh r3, [r3]
    cmp r3, #4                  @ {v:N} variable tag (0x0004, var_id): +8 units if it holds a name code
    addeq r1, r1, #4
    addeq r2, r2, #2
    beq ss_loop
    sub r3, r3, #0xE000
    cmp r3, #4                  @ {the} {The} {a} {A} {pl}: +2 units
    addls r1, r1, #1
    sub r3, r3, #0x10
    cmp r3, #15                 @ 0xE010..0xE01F literal party name code: +8 units
    addls r1, r1, #4
    add r2, r2, #1
    b ss_loop
ss_done:
    add r1, r0, r1, lsl #1
    uxth r1, r1                 @ the replaced instruction, with the slack
    pop {r2, r3, pc}

pane_fix:                       @ r4 = pane -> r0 = text length
    push {r4, lr}
    ldr r0, [r4, #0xa4]
    ldrh r1, [r4, #0xcc]
    cmp r0, #0
    cmpne r1, #1
    bls pf_none
    push {r0, r1}
    bl NAMESADDR                @ namefix.names: party name codes -> the names
    pop {r0, r1}
    bl gfix
    ldr r0, [r4, #0xa4]
    mov r1, #0
pf_len:
    ldrh r2, [r0], #2
    cmp r2, #0
    addne r1, r1, #1
    bne pf_len
    strh r1, [r4, #0xce]
    mov r0, r1
    pop {r4, pc}
pf_none:
    ldrh r0, [r4, #0xce]
    pop {r4, pc}

walker:
    .word WALKADDR
walker_len:
    .word WALKLEN
"""


def _stub_source() -> str:
    from .namefix import CAVE as NAMES
    return (STUB.replace("WALKADDR", f"{WALKER:#x}")
            .replace("WALKLEN", f"{WALKER_END - WALKER:#x}").replace("NAMESADDR", f"{NAMES:#x}"))


def _full_source() -> str:
    """Stubs, gfix and its data, as assembled at CAVE."""
    ends = sorted(LABELS.values()) + [len(DATA)]
    tail = ""
    for k, v in sorted(LABELS.items(), key=lambda kv: kv[1]):
        tail += k + ":\n    .byte " + ", ".join(str(x) for x in DATA[v:ends[ends.index(v) + 1]]) + "\n"
    return _stub_source() + SOURCE + tail


def assemble_patch() -> tuple[bytes, list[tuple[int, bytes, bytes]]]:
    """(cave code: stubs + gfix + data at CAVE, [(hook address, original, bl instruction)])."""
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    src = _full_source()
    cave = bytes(ks.asm(src, CAVE)[0])
    labels = _label_addresses(src)
    hooks = [(h, bytes.fromhex(orig), bytes(ks.asm(f"bl {labels[name]:#x}", h)[0]))
             for h, orig, name in HOOKS]
    return cave, hooks


def _label_addresses(src: str) -> dict[str, int]:
    """Address of every top-level label of src assembled at CAVE."""
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    import re
    nl = chr(10)
    label = re.compile(r"^([A-Za-z_]\w*):")
    out = {}
    lines = src.splitlines()
    for i, line in enumerate(lines):
        m = label.match(line)
        if m:
            # the code before the label, with the labels from here on defined after it
            later = [label.match(x).group(1) for x in lines[i:] if label.match(x)]
            body = nl.join(lines[:i]) + nl + "".join(x + ":" + nl for x in later) + "    nop" + nl
            out[m.group(1)] = CAVE + len(ks.asm(body, CAVE)[0]) - 4
    return out


def write_code_module(path: str):
    cave, hooks = assemble_patch()
    lines = [cave[i:i + 48].hex() for i in range(0, len(cave), 48)]
    with open(path, "w", encoding="utf-8") as fp:
        fp.write('"""Generated by `python -m dq11tool.grammar_asm --write` from grammar_asm.py: do not edit."""\n')
        fp.write(f"CAVE = {CAVE:#x}\n")
        fp.write("HOOKS = [  # (address, original instruction, bl to its stub)\n" + "".join(
            f'    ({h:#x}, bytes.fromhex("{o.hex()}"), bytes.fromhex("{b.hex()}")),\n' for h, o, b in hooks)
            + "]\n")
        fp.write("CODE = bytes.fromhex(\n" + "\n".join(f'    "{l}"' for l in lines) + ")\n")
        fp.write(f"GFIX = {CAVE + _gfix_offset():#x}\n")


def _gfix_offset() -> int:
    return _label_addresses(_full_source())["gfix"] - CAVE


if __name__ == "__main__":
    import os
    import sys
    if "--write" in sys.argv:
        write_code_module(os.path.join(os.path.dirname(os.path.abspath(__file__)), "grammar_code.py"))
    cave, hooks = assemble_patch()
    print(len(cave), "bytes at", hex(CAVE), "gfix at", hex(CAVE + _gfix_offset()),
          "hooks", [(hex(h), b.hex()) for h, _, b in hooks])
