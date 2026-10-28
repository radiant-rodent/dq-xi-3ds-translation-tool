"""Shrink-to-fit for layout text, at runtime: one-line labels and multi-line text boxes.

The game draws a text box string with a TextWriter set up from the pane (TextBox vt+0x70,
0x53F078: font size -> x/y scale at writer+8/+0xC, character spacing writer+0x34, line
spacing writer+0x38, width limit writer+0x30 = the pane width [pane+0x3C]). English text
wider than its pane wraps onto a second line of a one-line label (over the next row: "No
Fleeing from" / "Battle" over "No Shopping"), runs into its neighbour ("Beguilement
Protection" over the "20%" next to it), or a description wraps to more lines than its box has
(2D battle: the 90x40 spell box needs 6 lines of the wide 2D font).

This patch measures each string when its vertex data is built (0x53F518, right after the
writer setup) and narrows only that string, so it fits its pane. It does not scale the
glyphs (a fractional x scale resamples them: blurry, worst with the 2D pixel fonts, user
2026-10-20 / 2026-10-22) but switches the string to narrower glyphs: every font carries
fontnarrow.LEVELS condensed copies of its Latin letters, level L of character c at
U+E100 + L * 0x100 + c. The string is rewritten in place in the pane's text buffer (REMAP),
level by level, until it fits:

    0x53F584  b FIT                          (was: ldr r0, [r4, #0xF8])
    FIT (CAVE): only for panes the exporter marked (user data at pane+0x99: the 8-byte user
        data field of the layout's pane header, empty in every original layout) and strings
        with characters that have narrow glyphs (Japanese is never touched). The string
        [pane+0xA4], length [pane+0xCE], is first mapped back to plain characters (a text
        narrowed earlier is laid out again), then:
      "fit" (one-line label): writer+0x30 = FLT_MAX (never wraps); while
        w = CalcStringWidth(writer, str, len) (0x713344) > pane width * MARGIN: next level;
        past the last level: x scale and character spacing *= max(usable / w, MIN_FACTOR)
      "fim" (multi-line box): the text wraps at the pane width as before; while its wrapped
        rect (CalcStringRect 0x6EFF84) is higher than the pane [pane+0x40] or wider than it
        [pane+0x3C] (a word longer than the line is not wrapped, 2026-10-25): next level;
        past the last one f *= STEP (at most STEPS times, not below MIN_FACTOR) on a copy of
        the writer, then x and y scale, character and line spacing *= f (1.0 if it fit)
      "fl1".."fl4": always that level (a pane the exporter sized for it: the lower-screen
        2D talk window, export.TALK_LOWER_2D)
      then: ldr r0, [r4, #0xF8] ; b 0x53F588
    REMAP (REMAP_AT, r5 string, r6 length, r8 level): every character with narrow glyphs
        (BITMAP, fontnarrow.CHARS) becomes its level-r8 code, level 0 its plain character;
        the character after U+0004 (a variable's argument) stays. Returns the number of such
        characters. Spaces keep U+0020: the runtime word wrap breaks lines there.
The pane and every other string shown in it keep their size.

Which panes are marked is decided by the exporter (Bflyt.mark_fit): panes outside the message
windows (their lines wrap and page on purpose); one-line ones (height below two lines of its
font) get "fit", taller ones "fim" when their Japanese placeholder fits the box.

Code: SOURCE at CAVE (0x724C00-0x724DC0), REMAP_SOURCE at REMAP_AT (0x724F98-0x725000, after
namefix's table), the 32-byte BITMAP at 0x7249E0 (after grammar_code); `python -m
dq11tool.autofit` reassembles them (keystone) and checks CODE / REMAP_CODE.
"""
from __future__ import annotations

BASE = 0x100000
HOOK_AT = 0x53F584
ORIGINAL = bytes.fromhex("f80094e5")          # ldr r0, [r4, #0xf8]
RETURN_TO = 0x53F588
CALC_STRING_WIDTH = 0x713344
CALC_STRING_RECT = 0x6EFF84                   # (writer, rect out: left, top, right, bottom, str, len)
MEMCPY = 0x116C10
CAVE = 0x724C00                               # .text tail (below 0x724380: test builds only, luma.py)
CAVE_END = 0x724DC0                           # namefix.CAVE
REMAP_AT = 0x724F98                           # after namefix's table (namefix.TABLE_END)
REMAP_END = 0x725000                          # end of .text
SCALE_AT = 0x724BBC                           # after wordwrap's code, before CAVE
SCALE_END = 0x724C00
RECT_WIDTH_AT = SCALE_AT + 0x34               # rect_width, after scale_writer in SCALE_CODE
BITMAP_AT = 0x7249E0                          # after grammar_code (ends 0x7249E0), before wordwrap
MARKER = b"fit"                               # one-line label
MARKER_MULTI = b"fim"                         # multi-line box
MARKER_LEVEL = b"fl"                          # + level digit: always that level
MARGIN = 0.98
MIN_FACTOR = 0.55
STEP = 0.92
STEPS = 8
LEVELS = 4                                    # len(fontnarrow.LEVELS)

SOURCE = f"""
fit:
    ldrb r0, [r4, #0x99]
    cmp r0, #0x66
    bne leave
    ldrb r0, [r4, #0x9a]
    cmp r0, #0x69
    cmpne r0, #0x6c
    bne leave
    push {{r4-r10, lr}}
    vpush {{d8-d10}}
    sub sp, sp, #0x60
    add r7, sp, #{0x60 + 24 + 32:#x}
    ldr r5, [r4, #0xa4]
    ldrh r6, [r4, #0xce]
    cmp r5, #0
    cmpne r6, #0
    beq out
    mov r8, #0
    bl {REMAP_AT:#x}
    cmp r0, #0
    beq out
    vldr s18, min_factor
    ldrb r0, [r4, #0x9b]
    cmp r0, #0x74
    beq label
    cmp r0, #0x6d
    beq multi
    sub r8, r0, #0x30
    cmp r8, #{LEVELS}
    blls {REMAP_AT:#x}
    b out
label:
    mov r0, #0x7f000000       @ 1.7e38: the line never wraps
    str r0, [r7, #0x30]
    vldr s16, [r4, #0x3c]
    vldr s1, margin
    vmul.f32 s16, s16, s1
o_loop:
    mov r0, r7
    mov r1, r5
    mov r2, r6
    bl {CALC_STRING_WIDTH:#x}
    vcmpe.f32 s0, s16
    vmrs APSR_nzcv, fpscr
    ble out
    cmp r8, #{LEVELS}
    beq o_scale
    add r8, r8, #1
    bl {REMAP_AT:#x}
    b o_loop
o_scale:
    vdiv.f32 s0, s16, s0
    vcmpe.f32 s0, s18
    vmrs APSR_nzcv, fpscr
    vmovlt.f32 s0, s18
    vldr s1, [r7, #8]
    vmul.f32 s1, s1, s0
    vstr s1, [r7, #8]
    vldr s1, [r7, #0x34]
    vmul.f32 s1, s1, s0
    vstr s1, [r7, #0x34]
    b out
multi:                        @ next level while too high or too wide, then scale steps
    vldr s20, [r4, #0x40]
    vldr s16, one             @ not vmov #1.0: VFPv3, undefined on the 3DS (VFPv2)
    mov r10, #{STEPS}
m_lvl:
    bl wrapped_h
    ble m_done                @ fits (scaling by s16 = 1.0 changes nothing)
    cmp r8, #{LEVELS}
    beq m_step
    add r8, r8, #1
    bl {REMAP_AT:#x}
    b m_lvl
m_step:
    vldr s1, step
    vmul.f32 s16, s16, s1
    vcmpe.f32 s16, s18
    vmrs APSR_nzcv, fpscr
    vmovlt.f32 s16, s18
    blt m_done
    subs r10, r10, #1
    bne m_lvl
m_done:
    mov r0, r7
    bl {SCALE_AT:#x}
out:
    add sp, sp, #0x60
    vpop {{d8-d10}}
    pop {{r4-r10, lr}}
leave:
    ldr r0, [r4, #0xf8]
    b {RETURN_TO:#x}
wrapped_h:                    @ gt: the wrapped text, measured with a copy of the writer, does not fit
    push {{r0, lr}}             @ scaled by s16 (measuring moves the writer's pen: the real one
    add r0, sp, #8            @ would draw the text lower); 8 bytes keep the stack aligned
    mov r1, r7
    mov r2, #0x48
    bl {MEMCPY:#x}
    add r0, sp, #8
    bl {SCALE_AT:#x}
    add r0, sp, #8
    add r1, sp, #0x50
    mov r2, r5
    mov r3, r6
    bl {CALC_STRING_RECT:#x}
    vldr s0, [sp, #0x54]
    vldr s1, [sp, #0x5c]
    vsub.f32 s0, s1, s0
    vabs.f32 s0, s0
    vcmpe.f32 s0, s20         @ gt: higher than the pane ...
    vmrs APSR_nzcv, fpscr
    bgt wh_out
    bl {RECT_WIDTH_AT:#x}     @ ... or wider: one word longer than the line is not wrapped
    vldr s1, [r4, #0x3c]      @ ("eggsoskeleton" ran into the next 78 px label, 2026-10-25)
    vcmpe.f32 s0, s1
    vmrs APSR_nzcv, fpscr
wh_out:
    pop {{r0, pc}}
margin:
    .float {MARGIN}
min_factor:
    .float {MIN_FACTOR}
step:
    .float {STEP}
one:
    .float 1.0
"""

# r0 writer: x and y scale, character and line spacing *= s16
SCALE_SOURCE = """
scale_writer:
    vldr s0, [r0, #8]
    vmul.f32 s0, s0, s16
    vstr s0, [r0, #8]
    vldr s0, [r0, #0xc]
    vmul.f32 s0, s0, s16
    vstr s0, [r0, #0xc]
    vldr s0, [r0, #0x34]
    vmul.f32 s0, s0, s16
    vstr s0, [r0, #0x34]
    vldr s0, [r0, #0x38]
    vmul.f32 s0, s0, s16
    vstr s0, [r0, #0x38]
    bx lr
rect_width:                   @ width of the rect wrapped_h measured (its sp + 0x50: l, t, r, b)
    vldr s0, [sp, #0x50]
    vldr s1, [sp, #0x58]
    vsub.f32 s0, s1, s0
    bx lr
"""

# r5 string, r6 length (UTF-16 units), r8 level -> r0 characters with narrow glyphs;
# uses r1-r3, ip, r10
REMAP_SOURCE = f"""
remap:
    mov r0, #0
    mov r1, r5
    add r10, r5, r6, lsl #1
r_loop:
    cmp r1, r10
    bxhs lr
    ldrh r3, [r1], #2
    cmp r3, #4
    addeq r1, r1, #2
    beq r_loop
    sub r2, r3, #0xe200
    cmp r2, #0x400
    andlo r3, r3, #0xff
    cmp r3, #0x100
    bhs r_loop
    ldr ip, bitmap
    ldrb r2, [ip, r3, lsr #3]
    and ip, r3, #7
    mov r2, r2, lsr ip
    tst r2, #1
    addne r0, r0, #1
    cmpne r8, #0
    addne r3, r3, r8, lsl #8
    addne r3, r3, #0xe100
    strh r3, [r1, #-2]
    b r_loop
bitmap:
    .word {BITMAP_AT:#x}
"""


def _bitmap() -> bytes:
    from .fontnarrow import bitmap
    return bitmap()


def assemble(source: str = SOURCE, base: int = CAVE) -> bytes:
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    enc, _ = ks.asm(source, base)
    return bytes(enc)


def jump(src: int, dst: int) -> bytes:
    off = (dst - (src + 8)) >> 2
    return (0xEA000000 | (off & 0xFFFFFF)).to_bytes(4, "little")


def _parts() -> list[tuple[int, int, bytes]]:
    return [(CAVE, CAVE_END, CODE), (SCALE_AT, SCALE_END, SCALE_CODE), (REMAP_AT, REMAP_END, REMAP_CODE),
            (BITMAP_AT, BITMAP_AT + 32, _bitmap())]


def patch(code: bytes) -> tuple[bytes, list[str]]:
    """Returns (code, problems)."""
    out = bytearray(code)
    h = HOOK_AT - BASE
    j = jump(HOOK_AT, CAVE)
    parts = _parts()
    if out[h:h + 4] == j and all(out[a - BASE:a - BASE + len(b)] == b for a, _, b in parts):
        return bytes(out), []
    if out[h:h + 4] != ORIGINAL:
        return bytes(out), [f"autofit: unexpected code at {HOOK_AT:#x}"]
    for a, e, b in parts:
        if a + len(b) > e:
            return bytes(out), [f"autofit: code at {a:#x} is {len(b)} bytes, room for {e - a}"]
        if any(out[a - BASE:a - BASE + len(b)]):
            return bytes(out), [f"autofit: cave {a:#x} is not free"]
    for a, _, b in parts:
        out[a - BASE:a - BASE + len(b)] = b
    out[h:h + 4] = j
    return bytes(out), []


def assemble_all() -> dict[str, bytes]:
    return {"CODE": assemble(), "SCALE_CODE": assemble(SCALE_SOURCE, SCALE_AT),
            "REMAP_CODE": assemble(REMAP_SOURCE, REMAP_AT)}


CODE = bytes.fromhex(
    "9900d4e5660050e34d00001a9a00d4e5690050e36c0050134900001af0472de9068b2ded60d04de298708de2"
    "a45094e5be6cd4e1000055e3000056133d00000a0080a0e3d30000eb000050e33900000a569a9fed9b00d4e5"
    "740050e30500000a6d0050e31f00000a308040e2040058e3c800009b2f0000ea7f04a0e3300087e50f8a94ed"
    "480adfed208a28ee0700a0e10510a0e10620a0e1a9b9ffebc80ab4ee10faf1ee230000da040058e30200000a"
    "018088e2b70000ebf3ffffea000a88eec90ab4ee10faf1ee490ab0be020ad7ed800a60ee020ac7ed0d0ad7ed"
    "800a60ee0d0ac7ed130000ea10aa94ed318a9fed08a0a0e3140000eb0c0000da040058e30200000a018088e2"
    "a20000ebf8ffffea270adfed208a28eec98ab4ee10faf1ee498ab0be010000ba01a05ae2f0ffff1a0700a0e1"
    "a0ffffeb60d08de2068bbdecf047bde8f80094e50e6af8ea01402de908008de20710a0e14820a0e3abc7e7eb"
    "08008de294ffffeb08008de250108de20520a0e10630a0e1812cffeb150a9ded170addedc00a30eec00ab0ee"
    "ca0ab4ee10faf1ee030000ca94ffffeb0f0ad4ede00ab4ee10faf1ee0180bde848e17a3fcdcc0c3f1f856b3f"
    "0000803f")

SCALE_CODE = bytes.fromhex(
    "020a90ed080a20ee020a80ed030a90ed080a20ee030a80ed0d0a90ed080a20ee0d0a80ed0e0a90ed080a20ee"
    "0e0a80ed1eff2fe1140a9ded160addedc00a30ee1eff2fe1")

REMAP_CODE = bytes.fromhex(
    "0000a0e30510a0e186a085e00a0051e11eff2f21b230d1e0040053e302108102f9ffff0ae22c43e2010b52e3"
    "ff300332010c53e3f4ffff2a24c09fe5a321dce707c003e2322ca0e1010012e3010080120000581308348310"
    "e13c8312b23041e1e9ffffeae0497200")

if __name__ == "__main__":
    import re
    import sys
    new = assemble_all()
    room = {"CODE": CAVE_END - CAVE, "SCALE_CODE": SCALE_END - SCALE_AT, "REMAP_CODE": REMAP_END - REMAP_AT}
    for k, v in new.items():
        print(f"{k}: {len(v)} bytes, room {room[k]}" + ("" if v == globals()[k] else "  (stale)"))
    if "--write" in sys.argv:
        src = open(__file__, encoding="utf-8").read()
        for k, v in new.items():
            hx = v.hex()
            lines = "".join('\n    "' + hx[i:i + 88] + '"' for i in range(0, len(hx), 88))
            src = re.sub(rf"^{k} = .*?(?=\n\n)", lambda m: f"{k} = bytes.fromhex({lines})", src,
                         count=1, flags=re.M | re.S)
        open(__file__, "w", encoding="utf-8").write(src)
