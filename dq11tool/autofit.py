"""Shrink-to-fit for layout text, at runtime: one-line labels and multi-line text boxes.

The game draws a text box string with a TextWriter set up from the pane (TextBox vt+0x70,
0x53F078: font size -> x/y scale at writer+8/+0xC, character spacing writer+0x34, line
spacing writer+0x38, width limit writer+0x30 = the pane width [pane+0x3C]). English text
wider than its pane wraps onto a second line of a one-line label (over the next row: "No
Fleeing from" / "Battle" over "No Shopping"), runs into its neighbour ("Beguilement
Protection" over the "20%" next to it), or a description wraps to more lines than its box has
(2D battle: the 90x40 spell box needs 6 lines of the wide 2D font). One static squeeze per
pane (export.RUNTIME_FIT) narrows every string shown in it to the widest one.

This patch measures each string when its vertex data is built (0x53F518, right after the
writer setup) and narrows only that string, so it fits its pane:

    0x53F584  b FIT                          (was: ldr r0, [r4, #0xF8])
    FIT (CAVE): only for panes the exporter marked (user data at pane+0x99: the 8-byte user
        data field of the layout's pane header, empty in every original layout) and strings
        with a Latin letter (Japanese, numbers and symbols are never touched):
      "fit" (one-line label): writer+0x30 = FLT_MAX (never wraps);
        w = CalcStringWidth(writer, str [pane+0xA4], len [pane+0xCE])   (0x713344);
        usable = pane width * MARGIN; if w > usable: f = max(usable / w, MIN_FACTOR):
        x scale and character spacing *= f
      "fim" (multi-line box): the text wraps at the pane width as before; while the wrapped
        height (CalcStringRect 0x6EFF84 on a copy of the writer) is more than the pane height
        [pane+0x40], f *= STEP (at most STEPS times, not below MIN_FACTOR); then x and y scale,
        character and line spacing *= f (more words per line, smaller lines)
      then: ldr r0, [r4, #0xF8] ; b 0x53F588
The string is laid out with the narrowed writer (alignment included: the writer measures
the lines again), the pane and every other string shown in it keep their size.

Which panes are marked is decided by the exporter (Bflyt.mark_fit): panes outside the message
windows (their lines wrap and page on purpose); one-line ones (height below two lines of its
font) get "fit", taller ones "fim" when their Japanese placeholder fits the box.
"""
from __future__ import annotations

BASE = 0x100000
HOOK_AT = 0x53F584
ORIGINAL = bytes.fromhex("f80094e5")          # ldr r0, [r4, #0xf8]
RETURN_TO = 0x53F588
CALC_STRING_WIDTH = 0x713344
CALC_STRING_RECT = 0x6EFF84                   # (writer, rect out: left, top, right, bottom, str, len)
MEMCPY = 0x116C10
CAVE = 0x724040                               # free .text tail (codestr strings live in .rodata)
MARKER = b"fit"                               # one-line label
MARKER_MULTI = b"fim"                         # multi-line box
MARGIN = 0.98
MIN_FACTOR = 0.55
STEP = 0.92
STEPS = 8

SOURCE = f"""
fit:
    ldrb r0, [r4, #0x99]
    cmp r0, #0x66
    bne leave
    ldrb r0, [r4, #0x9a]
    cmp r0, #0x69
    bne leave
    ldrb r0, [r4, #0x9b]
    cmp r0, #0x74
    cmpne r0, #0x6d
    bne leave
    push {{r4-r10, lr}}
    vpush {{d8-d10}}
    sub sp, sp, #0x60
    add r7, sp, #{0x60 + 24 + 32:#x}
    mov r9, r0
    ldr r5, [r4, #0xa4]
    ldrh r6, [r4, #0xce]
    cmp r5, #0
    beq out
    cmp r6, #0
    beq out
    mov r0, #0
scan:
    add r1, r5, r0, lsl #1
    ldrh r1, [r1]
    orr r1, r1, #0x20
    sub r1, r1, #0x61
    cmp r1, #25
    bls latin
    add r0, r0, #1
    cmp r0, r6
    blo scan
    b out
latin:
    vldr s18, min_factor
    cmp r9, #0x6d
    beq multi
    ldr r0, flt_max
    str r0, [r7, #0x30]
    mov r0, r7
    mov r1, r5
    mov r2, r6
    bl {CALC_STRING_WIDTH:#x}
    vldr s16, [r4, #0x3c]
    vldr s1, margin
    vmul.f32 s16, s16, s1
    vcmpe.f32 s0, s16
    vmrs APSR_nzcv, fpscr
    ble out
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
multi:
    vldr s20, [r4, #0x40]
    vldr s16, one             @ not vmov #1.0: VFPv3, undefined on the 3DS (VFPv2)
    mov r8, #{STEPS}
m_loop:
    mov r0, sp
    mov r1, r7
    mov r2, #0x48
    bl {MEMCPY:#x}
    mov r0, sp
    bl scale_writer
    mov r0, sp
    add r1, sp, #0x48
    mov r2, r5
    mov r3, r6
    bl {CALC_STRING_RECT:#x}
    vldr s0, [sp, #0x4c]
    vldr s1, [sp, #0x54]
    vsub.f32 s0, s1, s0
    vabs.f32 s0, s0
    vcmpe.f32 s0, s20
    vmrs APSR_nzcv, fpscr
    ble m_done
    vldr s1, step
    vmul.f32 s16, s16, s1
    vcmpe.f32 s16, s18
    vmrs APSR_nzcv, fpscr
    vmovlt.f32 s16, s18
    blt m_done
    subs r8, r8, #1
    bne m_loop
m_done:
    mov r0, r7
    bl scale_writer
out:
    add sp, sp, #0x60
    vpop {{d8-d10}}
    pop {{r4-r10, lr}}
leave:
    ldr r0, [r4, #0xf8]
    b {RETURN_TO:#x}
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
flt_max:
    .word 0x7f7fffff
margin:
    .float {MARGIN}
min_factor:
    .float {MIN_FACTOR}
step:
    .float {STEP}
one:
    .float 1.0
"""

# assemble(CAVE) (keystone, emutest only); checked by emutest/autofittest.py
CODE = bytes.fromhex(
    "9900d4e5660050e35800001a9a00d4e5690050e35500001a9b00d4e5740050e36d0050135100001af0472de9"
    "068b2ded60d04de298708de20090a0e1a45094e5be6cd4e1000055e34500000a000056e34300000a0000a0e3"
    "801085e0b010d1e1201081e3611041e2190051e30300009a010080e2060050e1f6ffff3a380000ea4b9a9fed"
    "6d0059e31600000a18019fe5300087e50700a0e10510a0e10620a0e197bcffeb0f8a94ed400adfed208a28ee"
    "c80ab4ee10faf1ee290000da000a88eec90ab4ee10faf1ee490ab0be020ad7ed800a60ee020ac7ed0d0ad7ed"
    "800a60ee0d0ac7ed1e0000ea10aa94ed328a9fed0880a0e30d00a0e10710a0e14820a0e3b2cae7eb0d00a0e1"
    "1a0000eb0d00a0e148108de20520a0e10630a0e1882fffeb130a9ded150addedc00a30eec00ab0eeca0ab4ee"
    "10faf1ee070000da1d0adfed208a28eec98ab4ee10faf1ee498ab0be010000ba018058e2e5ffff1a0700a0e1"
    "040000eb60d08de2068bbdecf047bde8f80094e5f36cf8ea020a90ed080a20ee020a80ed030a90ed080a20ee"
    "030a80ed0d0a90ed080a20ee0d0a80ed0e0a90ed080a20ee0e0a80ed1eff2fe1ffff7f7f48e17a3fcdcc0c3f"
    "1f856b3f0000803f")


def assemble(base: int = CAVE) -> bytes:
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    enc, _ = ks.asm(SOURCE, base)
    return bytes(enc)


def jump(src: int, dst: int) -> bytes:
    off = (dst - (src + 8)) >> 2
    return (0xEA000000 | (off & 0xFFFFFF)).to_bytes(4, "little")


def patch(code: bytes) -> tuple[bytes, list[str]]:
    """Returns (code, problems)."""
    out = bytearray(code)
    h, c = HOOK_AT - BASE, CAVE - BASE
    j = jump(HOOK_AT, CAVE)
    if out[h:h + 4] == j and out[c:c + len(CODE)] == CODE:
        return bytes(out), []
    if out[h:h + 4] != ORIGINAL:
        return bytes(out), [f"autofit: unexpected code at {HOOK_AT:#x}"]
    if any(out[c:c + len(CODE)]):
        return bytes(out), [f"autofit: cave {CAVE:#x} is not free"]
    out[c:c + len(CODE)] = CODE
    out[h:h + 4] = j
    return bytes(out), []
