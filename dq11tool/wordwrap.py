"""Word wrap for text the game lays out at runtime (battle messages with names, ...).

The text writer (0x6F00D8) breaks a line wherever the next glyph would pass the line width
(0x6F0514: x + width - line start > [writer + 0x30] -> the glyph becomes a newline and is read
again). Japanese has no spaces, so it breaks inside English words ("...takes 2 poi / nts").
Strings from the tables are wrapped by the exporter already; this only matters for strings
the game puts together (names inserted into templates) and the reflowed descriptions.

The patch looks ahead at every space and breaks the line there (the space becomes the
newline, the next line starts with the word) when
  - the next word does not fit on the line any more, or
  - the rest of the line (up to the end of the string or an explicit newline / control code)
    does not fit, and the line so far is at least as wide as that rest: two balanced lines
    ("The malicious drackyma takes / 1 point of damage!") instead of a filled first line and a
    lone word under it, which looks broken in the centred battle captions (user report
    2026-10-15). Text of three or more lines fills its lines and balances the last two. The
    balancing break skips a space before a one-character word ("drackyma A" stays together).
Widths: font->vt[0x3C](font, char) times the x scale [obj + 8], plus the character spacing
[obj + 0x34] per character; pen minus line start is the width used so far, [obj + 0x30] the
line width. A single word longer than the line still breaks the way it did.

Two hooks, the same decision (otherwise the window sizes its lines differently from how they
are drawn):
    0x6F0514  b writer      (was: cmp r6, #0)  - the writer: r4 writer, r6 wrapping on,
        r7 string, sb length, sl current character, fp start of the line, r8 character,
        [sp + 0x60] read pointer (after r8), pen s17 + s18, line start s22; a break stores 0
        at [sp + 0x90], sets r8 = 0xA and takes the newline path 0x6F05F8.
    0x6EFEB4  b measurer    (was: vldr s1, [r5, #0x30]) - the line measurer 0x6EFB18 (message
        windows lay their lines out with it): r5 writer, r6 character, sb end of the string,
        [sp + 0x2C] read pointer, pen s17, line start s19; a break sets r6 = 0xA,
        [sp + 0x38] = 1, [sp + 0x5C] = 0 and goes to 0x6EFF54.
Code: SOURCE below at CAVE (0x724A00-0x724BBC in codestr's reserved text tail);
`python -m dq11tool.wordwrap --write` reassembles it (keystone) and checks CODE.
"""
from __future__ import annotations

BASE = 0x100000
CAVE = 0x724A00
CAVE_END = 0x724BBC          # autofit.SCALE_AT
HOOK_AT, ORIGINAL = 0x6F0514, bytes.fromhex("000056e3")     # cmp r6, #0
HOOK2_AT, ORIGINAL2 = 0x6EFEB4, bytes.fromhex("0c0ad5ed")   # vldr s1, [r5, #0x30]

SOURCE = """
writer:
    cmp r8, #0x20
    bne w_orig
    cmp r6, #0
    beq w_orig
    cmp sl, fp
    beq w_orig
    push {r0, r1, r2, r3, ip, lr}
    vpush {d0, d1, d2, d3}
    mov r0, r4
    ldr r1, [sp, #0x98]
    add r2, r7, sb, lsl #1
    bl measure
    vldr s1, [r4, #8]
    vldr s3, [r4, #0x34]
    vldr s6, [r4, #0x30]
    vadd.f32 s5, s17, s18
    vsub.f32 s5, s5, s22
    bl decide
    vpop {d0, d1, d2, d3}
    pop {r0, r1, r2, r3, ip, lr}
    beq w_orig
    mov r0, #0
    strh r0, [sp, #0x90]
    mov r8, #0xa
    b 0x6f05f8
w_orig:
    cmp r6, #0
    b 0x6f0518

measurer:
    cmp r6, #0x20
    bne m_orig
    push {r0, r1, r2, r3, ip, lr}
    vpush {d0, d1, d2, d3}
    mov r0, r5
    ldr r1, [sp, #0x64]
    mov r2, sb
    bl measure
    vldr s1, [r5, #8]
    vldr s3, [r5, #0x34]
    vldr s6, [r5, #0x30]
    vsub.f32 s5, s17, s19
    bl decide
    vpop {d0, d1, d2, d3}
    pop {r0, r1, r2, r3, ip, lr}
    beq m_orig
    mov r0, #0
    mov r1, #1
    mov r6, #0xa
    str r1, [sp, #0x38]
    strh r0, [sp, #0x5c]
    b 0x6eff54
m_orig:
    vldr s1, [r5, #0x30]
    b 0x6efeb8

@ decide: r0/r1 = glyph width sum / count of the next word, r2/r3 = of the rest of the line,
@ s1 = x scale, s3 = character spacing, s5 = pen - line start, s6 = line width.
@ Z clear (ne) = break here. No balancing break before a one-character word: the letter of a
@ monster group ("drackyma A") stays with its name.
decide:
    vmov s0, r0
    vcvt.f32.s32 s0, s0
    vmul.f32 s0, s0, s1
    vmov s2, r1
    vcvt.f32.s32 s2, s2
    vmla.f32 s0, s2, s3
    vmov s4, r2
    vcvt.f32.s32 s4, s4
    vmul.f32 s4, s4, s1
    vmov s2, r3
    vcvt.f32.s32 s2, s2
    vmla.f32 s4, s2, s3
    vadd.f32 s0, s0, s5
    vcmpe.f32 s0, s6
    vmrs apsr_nzcv, fpscr
    bgt d_break
    cmp r1, #1
    beq d_keep
    vadd.f32 s7, s4, s5
    vcmpe.f32 s7, s6
    vmrs apsr_nzcv, fpscr
    ble d_keep
    vcmpe.f32 s5, s4
    vmrs apsr_nzcv, fpscr
    bge d_break
d_keep:
    movs r0, #0
    bx lr
d_break:
    movs r0, #1
    bx lr

@ measure(r0 writer, r1 read pointer, r2 end): glyph widths from the read pointer to the end
@ of the line (end of the string, a newline or a control code); r0/r1 = sum / count up to the
@ first space (the next word), r2/r3 = sum / count of all of it.
measure:
    push {r4, r5, r6, r7, r8, sb, sl, fp, ip, lr}
    mov r4, r0
    mov r5, r1
    mov r6, r2
    mov r7, #0
    mov r8, #0
    mov sb, #0
    mov sl, #0
    mov fp, #0
m_loop:
    cmp r5, r6
    bhs m_out
    ldrh r1, [r5]
    cmp r1, #0x20
    blo m_out
    moveq fp, #1
    ldr r0, [r4, #0x24]
    ldr r3, [r0]
    ldr r3, [r3, #0x3c]
    blx r3
    add r7, r7, r0
    add r8, r8, #1
    cmp fp, #0
    addeq sb, sb, r0
    addeq sl, sl, #1
    add r5, r5, #2
    b m_loop
m_out:
    mov r0, sb
    mov r1, sl
    mov r2, r7
    mov r3, r8
    pop {r4, r5, r6, r7, r8, sb, sl, fp, ip, pc}
"""

CODE = bytes.fromhex(
    "200058e31600001a000056e31400000a0b005ae11200000a0f502de9080b2ded0400a0e198109de5892087e0"
    "430000eb020ad4ed0d1ad4ed0c3a94ed892a78eecb2a72ee200000eb080bbdec0f50bde80300000a0000a0e3"
    "b009cde10a80a0e3e42effea000056e3aa2effea200056e31300001a0f502de9080b2ded0500a0e164109de5"
    "0920a0e12c0000eb020ad5ed0d1ad5ed0c3a95ede92a78ee0a0000eb080bbdec0f50bde80500000a0000a0e3"
    "0110a0e30a60a0e338108de5bc05cde1232dffea0c0ad5edfa2cffea100a00eec00ab8ee200a20ee101a01ee"
    "c11ab8ee210a01ee102a02eec22ab8ee202a22ee103a01eec11ab8ee212a01ee220a30eec30ab4ee10faf1ee"
    "0a0000ca010051e30600000a223a72eec33af4ee10faf1ee020000dac22af4ee10faf1ee010000aa0000b0e3"
    "1eff2fe10100b0e31eff2fe1f05f2de90040a0e10150a0e10260a0e10070a0e30080a0e30090a0e300a0a0e3"
    "00b0a0e3060055e10e00002ab010d5e1200051e30b00003a01b0a003240094e5003090e53c3093e533ff2fe1"
    "007087e0018088e200005be30090890001a08a02025085e2eeffffea0900a0e10a10a0e10720a0e10830a0e1"
    "f09fbde8")
WRITER, MEASURER = 0x724A00, 0x724A6C
JUMP = bytes.fromhex("39d100ea")              # b WRITER    (at HOOK_AT)
JUMP2 = bytes.fromhex("ecd200ea")             # b MEASURER  (at HOOK2_AT)
HOOKS = [(HOOK_AT, ORIGINAL, JUMP), (HOOK2_AT, ORIGINAL2, JUMP2)]


def label_address(label: str) -> int:
    """Address of a label of SOURCE (every instruction is 4 bytes)."""
    n = 0
    for line in SOURCE.splitlines():
        s = line.split("@")[0].strip()
        if s == label + ":":
            return CAVE + 4 * n
        if s and not s.endswith(":"):
            n += 1
    raise KeyError(label)


def assemble() -> tuple[bytes, bytes, bytes]:
    """(CODE, JUMP, JUMP2) from SOURCE."""
    import keystone
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    code = bytes(ks.asm(SOURCE, CAVE)[0])
    j1 = bytes(ks.asm(f"b {label_address('writer'):#x}", HOOK_AT)[0])
    j2 = bytes(ks.asm(f"b {label_address('measurer'):#x}", HOOK2_AT)[0])
    return code, j1, j2


def patch(code: bytes) -> tuple[bytes, list[str]]:
    """Returns (code, problems)."""
    out = bytearray(code)
    c = CAVE - BASE
    if all(out[h - BASE:h - BASE + 4] == j for h, _, j in HOOKS) and out[c:c + len(CODE)] == CODE:
        return bytes(out), []
    problems = [f"word wrap: unexpected instruction at {h:#x} (different game version?)"
                for h, orig, _ in HOOKS if out[h - BASE:h - BASE + 4] != orig]
    if any(out[c:c + len(CODE)]):
        problems.append(f"word wrap: code cave at {CAVE:#x} is not free")
    if problems:
        return bytes(out), problems
    out[c:c + len(CODE)] = CODE
    for h, _, j in HOOKS:
        out[h - BASE:h - BASE + 4] = j
    return bytes(out), []


if __name__ == "__main__":
    import sys
    code, j1, j2 = assemble()
    assert CAVE + len(code) <= CAVE_END, "word wrap code runs into the next cave"
    assert (label_address("writer"), label_address("measurer")) == (WRITER, MEASURER)
    ok = (code, j1, j2) == (CODE, JUMP, JUMP2)
    print(f"{len(code)} bytes, {CAVE:#x}-{CAVE + len(code):#x}:", "CODE up to date" if ok else "CODE differs")
    if not ok and "--write" in sys.argv:
        print(code.hex(), j1.hex(), j2.hex())
    sys.exit(0 if ok else 1)
