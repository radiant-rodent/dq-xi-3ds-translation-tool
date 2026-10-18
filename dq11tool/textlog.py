"""Test builds only: log every text the game puts into a layout pane, for the emulator tests.

Every pane text passes the grammar stub at the end of both pane SetString functions
(grammar_asm.HOOKS -> grammar_code.CAVE, `bl gfix`). In a test build that `bl gfix` becomes
`bl logwrap`: logwrap records the text before the grammar pass (raw: tags U+E000.., control
codes) and, if the pass changed it, again after it (resolved), then returns to the stub.

Records go to a ring buffer the host sets up (emutest/textrec.py): the code only logs while
CTL+0 (ring address) is non-zero. The ring lives in heap memory the test build leaves unused
(export: `test_text_log` takes RING_RESERVE bytes from allocator 1's fallback heap without
giving them to the UI heap, see memfix.reserve_tail), so the game's own heaps are unchanged
except allocator 1, which has ~550 KB to spare.

CTL (0x839F20, after testhook.MAILBOX, up to the end of the .bss page at 0x83A000):
    +0  ring address (0 = logging off)     +4  ring size (bytes, multiple of 4)
    +8  write offset (published last)      +12 records written (sequence)
    +16 address of the u64 HID tick (0 = none; the host writes 0x10002000)
Dedupe cache: right after the ring (ring address + ring size), CACHE bytes, 32 slots of
    [pane, last frame, 4 hashes]: a pane set again to one of its last 4 texts within 2 frames
    is not logged (panes re-set every frame, also when built up piece by piece each frame,
    like "" -> "57" -> "57G"); the same text after a pause of > 2 frames is logged again.
Record (4-byte aligned; a u16 size of 0 means "wrapped, continue at offset 0"):
    +0 u16 size  +2 u16 units  +4 u32 seq  +8 u32 frame (testhook read counter, 30/s)
    +12 u32 pane  +16 u64 HID tick  +24 u16 start index  +26 u8 phase (0 raw, 1 resolved)
    +28 char[24] pane name (pane + 0x80)  +52 UTF-16 text (at most MAX_UNITS, no NUL)
"""
from __future__ import annotations

import struct

BASE = 0x100000
# in the debug probe area (testhook.PROBE_AREA, 0x724C00..testhook.HOOK): a test build has
# either the text log or the heap probes (autofit.CAVE took the room below grammar_code.CAVE)
CAVE = 0x724C00
CAVE_END = 0x724F00
CTL = 0x839F20
FRAME = 0x839F10             # testhook.MAILBOX + 16: hook read counter (one per frame)
HID_TICK = 0x10002000
RING_RESERVE = 0x40000          # 256 KB: a map reload sets thousands of layout texts in one frame
CACHE = 0x400                # end of the reserve: dedupe cache; the ring is the rest
MAX_UNITS = 0x400
HEADER = 52

SOURCE = r"""
logwrap:
    push {r0, r1, r8, r9, lr}
    mov r2, #0
    bl tlog
    mov r8, r0
    mov r9, r1
    ldr r0, [sp]
    ldr r1, [sp, #4]
    bl GFIX
    cmp r9, #0
    beq lw_out
    mov r2, #1
    mov r3, r8
    bl tlog
lw_out:
    pop {r0, r1, r8, r9, pc}

tlog:
    push {r4-r11, lr}
    ldr r6, ctl_addr
    ldr r7, [r6]
    cmp r7, #0
    beq t_out0
    ldr r8, [r4, #0xa4]
    ldrh r9, [r4, #0xcc]
    cmp r8, #0
    beq t_out0
    mov r10, #0
    mov r0, #0
th_loop:
    cmp r10, r9
    bhs th_done
    cmp r10, #MAXU
    bhs th_done
    add ip, r8, r10, lsl #1
    ldrh ip, [ip]
    cmp ip, #0
    beq th_done
    add r0, r0, r0, lsl #5
    eor r0, r0, ip
    add r10, r10, #1
    b th_loop
th_done:
    add r0, r0, r10
    cmp r2, #0
    beq t_dedupe
    cmp r0, r3
    beq t_out0
    b t_write
t_dedupe:
    ldr r11, [r6, #-16]
    ldr r1, [r6, #4]
    add r1, r7, r1
    eor ip, r4, r4, lsr #5
    mov ip, ip, lsr #4
    and ip, ip, #31
    add ip, ip, ip, lsl #1
    add r1, r1, ip, lsl #3
    ldr ip, [r1]
    cmp ip, r4
    bne t_newslot
    ldr ip, [r1, #4]
    str r11, [r1, #4]
    sub ip, r11, ip
    cmp ip, #2
    bhi t_reset
    ldr ip, [r1, #8]
    cmp ip, r0
    beq t_out0
    ldr ip, [r1, #12]
    cmp ip, r0
    beq t_out0
    ldr ip, [r1, #16]
    cmp ip, r0
    beq t_out0
    ldr ip, [r1, #20]
    cmp ip, r0
    beq t_out0
    ldr ip, [r1, #16]
    str ip, [r1, #20]
    ldr ip, [r1, #12]
    str ip, [r1, #16]
    ldr ip, [r1, #8]
    str ip, [r1, #12]
    str r0, [r1, #8]
    b t_write
t_newslot:
    str r4, [r1]
    str r11, [r1, #4]
t_reset:
    str r0, [r1, #8]
    mvn ip, #0
    str ip, [r1, #12]
    str ip, [r1, #16]
    str ip, [r1, #20]
t_write:
    mov r3, r0
    add r1, r10, r10
    add r1, r1, #HDR3
    bic r1, r1, #3
    ldr ip, [r6, #4]
    ldr r11, [r6, #8]
    add lr, r11, r1
    cmp lr, ip
    bls t_fits
    add lr, r11, #2
    cmp lr, ip
    movls lr, #0
    strhls lr, [r7, r11]
    mov r11, #0
t_fits:
    add lr, r7, r11
    strh r1, [lr]
    strh r10, [lr, #2]
    ldr ip, [r6, #12]
    str ip, [lr, #4]
    add ip, ip, #1
    str ip, [r6, #12]
    ldr ip, [r6, #-16]
    str ip, [lr, #8]
    str r4, [lr, #12]
    mov r0, #0
    str r0, [lr, #16]
    str r0, [lr, #20]
    ldr ip, [r6, #16]
    cmp ip, #0
    ldrne r0, [ip]
    strne r0, [lr, #16]
    ldrne r0, [ip, #4]
    strne r0, [lr, #20]
    strh r5, [lr, #24]
    strb r2, [lr, #26]
    mov r0, #0
    strb r0, [lr, #27]
    add r9, lr, #28
tn_loop:
    add ip, r4, #0x80
    ldrb ip, [ip, r0]
    strb ip, [r9, r0]
    add r0, r0, #1
    cmp r0, #24
    blo tn_loop
    mov r0, #0
    add r9, lr, #HDR
tu_loop:
    cmp r0, r10
    bhs tu_done
    add ip, r8, r0, lsl #1
    ldrh ip, [ip]
    add lr, r9, r0, lsl #1
    strh ip, [lr]
    add r0, r0, #1
    b tu_loop
tu_done:
    add r11, r11, r1
    str r11, [r6, #8]
    mov r0, r3
    mov r1, #1
    pop {r4-r11, pc}
t_out0:
    mov r1, #0
    pop {r4-r11, pc}
ctl_addr:
    .word CTLADDR
"""


def assemble(cave: int = CAVE, ctl: int = CTL, gfix: int | None = None) -> bytes:
    """logwrap + tlog at `cave`; gfix defaults to grammar_code.GFIX."""
    import keystone
    if gfix is None:
        from . import grammar_code
        gfix = grammar_code.GFIX
    src = (SOURCE.replace("GFIX", f"{gfix:#x}").replace("CTLADDR", f"{ctl:#x}")
           .replace("#MAXU", f"#{MAX_UNITS:#x}").replace("#HDR3", f"#{HEADER + 3}")
           .replace("#HDR", f"#{HEADER}"))
    if FRAME != ctl - 16 and ctl == CTL:
        raise AssertionError("FRAME must sit 16 bytes before CTL")
    ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_ARM)
    return bytes(ks.asm(src, cave)[0])


def _bl(at: int, target: int) -> bytes:
    return struct.pack("<I", 0xEB000000 | (((target - at - 8) >> 2) & 0xFFFFFF))


def patch(code: bytes) -> bytes:
    """Install the logger (after grammar.patch). Raises if the grammar stub or the cave is not
    as expected - a test build must not silently lose its text log."""
    from . import grammar_code as gc
    out = bytearray(code)
    body = assemble()
    c = CAVE - BASE
    if len(body) > CAVE_END - CAVE:
        raise ValueError(f"text log: code too big ({len(body)} bytes)")
    if any(out[c:c + len(body)]) and out[c:c + len(body)] != body:
        raise ValueError(f"text log: code cave at {CAVE:#x} is not free (debug probes? they share it)")
    g = gc.CAVE - BASE
    if out[g:g + len(gc.CODE)] != gc.CODE:
        raise ValueError("text log: needs the grammar pass (ExportOptions.grammar)")
    for at in range(gc.CAVE, gc.GFIX, 4):           # the stub's `bl gfix`
        if out[at - BASE:at - BASE + 4] == _bl(at, gc.GFIX):
            break
    else:
        raise ValueError("text log: `bl gfix` not found in the grammar stub")
    out[c:c + len(body)] = body
    out[at - BASE:at - BASE + 4] = _bl(at, CAVE)
    return bytes(out)


# ----------------------------------------------------------------------------- host side
def decode(rec: bytes) -> dict:
    size, n, seq, frame, pane, tick, start, phase = struct.unpack_from("<HHIIIQHB", rec)
    name = rec[28:52].split(b"\0")[0].decode("ascii", "replace")
    text = rec[HEADER:HEADER + 2 * n].decode("utf-16-le", "replace")
    return {"size": size, "seq": seq, "frame": frame, "pane": pane, "tick": tick,
            "start": start, "phase": phase, "name": name, "text": text}


def reference(texts: list[tuple[int, str, int, str]], ring_size: int = RING_RESERVE) -> list[dict]:
    """Python model of what the ARM code logs for a sequence of SetString calls
    [(pane, raw text, frame, resolved text)] (the selftest compares the two)."""
    cache = {}
    out = []

    def h(t):
        v = 0
        for ch in t[:MAX_UNITS]:
            v = ((v * 33) ^ ord(ch)) & 0xFFFFFFFF
        return (v + min(len(t), MAX_UNITS)) & 0xFFFFFFFF

    for pane, raw, frame, resolved in texts:
        hr = h(raw)
        slot = (((pane ^ (pane >> 5)) >> 4) & 31)
        e = cache.get(slot)
        if e and e[0] == pane:
            fresh = (frame - e[1]) & 0xFFFFFFFF <= 2
            e[1] = frame
            if fresh and hr in e[2]:
                continue
            e[2] = [hr] + e[2][:3] if fresh else [hr, None, None, None]
        else:
            cache[slot] = [pane, frame, [hr, None, None, None]]
        out.append({"pane": pane, "phase": 0, "text": raw[:MAX_UNITS], "frame": frame})
        if h(resolved) != hr:
            out.append({"pane": pane, "phase": 1, "text": resolved[:MAX_UNITS], "frame": frame})
    return out
