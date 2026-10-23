"""Test builds only: an input "mailbox" in RAM, so an emulator test can press buttons through
memory writes (Azahar RPC) instead of window input or a pre-recorded movie.

The SDK's nn::hid::PadReader::Read copies new HID entries (hold, trigger, release, circle
pad) into the caller's array and ends with `ldr r1, [sp, #0x20]` at RETURN_AT. That
instruction becomes a call to HOOK (zero padding after .text), which for every returned
entry ORs MAILBOX+0 into hold and MAILBOX+4 into trigger (trigger is then cleared: one
press), replaces the circle pad with MAILBOX+8 when bit 0 of MAILBOX+12 is set, and counts
calls in MAILBOX+16. A pulse (MAILBOX+20 buttons, MAILBOX+24 count) is held for exactly
`count` reads (the game reads once per frame) and then released by the hook itself: exact
short presses, independent of how fast the emulator runs. MAILBOX is the unused tail of the
.bss page.

Never part of a release build (ExportOptions.test_input_hook).

Hook source (keystone, ARM):
    push {r0, r2, r3, r4, lr}
    ldr r0, mbox
    ldr r2, [r0]            ; hold
    ldr r3, [r0, #4]        ; trigger (one-shot)
    if [r0+24] > 0 and sb > 0: [r0+24] -= 1; r2 |= [r0+20]   ; pulse
    mov r4, #0
    cmp sb, #0              ; sb = number of entries returned
    strgt r4, [r0, #4]
loop: cmp r4, sb / bge done
    add ip, r1, r4, lsl #4  ; r1 = PadStatus array (16-byte entries)
    [ip] |= r2 ; [ip+4] |= r3
    if [r0+12] & 1: [ip+12] = [r0+8]
    r4 += 1 / b loop
done: [r0+16] += 1
    pop {r0, r2, r3, r4, lr}
    ldr r1, [sp, #0x20]     ; the replaced instruction
    bx lr
mbox: .word MAILBOX
"""
from __future__ import annotations

BASE = 0x100000
RETURN_AT = 0x11AD3C
ORIGINAL = bytes.fromhex("20109de5")         # ldr r1, [sp, #0x20]
HOOK = 0x724F00
MAILBOX = 0x839F00
CODE = bytes.fromhex(
    "1d402de988009fe5002090e5043090e5184090e5000054e3050000da000059e3030000da014044e2184080e5"
    "144090e5042082e10040a0e3000059e3044080c5090054e10c0000aa04c281e000e09ce502e08ee100e08ce5"
    "04e09ce503e08ee104e08ce50ce090e501001ee308e090150ce08c15014084e2f0ffffea104090e5014084e2"
    "104080e51d40bde820109de51eff2fe1009f8300")
CALL = bytes.fromhex("6f2818eb")             # bl HOOK (from RETURN_AT)


PROBE_AREA = 0x724C00          # 0x724C00..HOOK: debug probes (codestr.RESERVED_TEXT_TAIL)
TRACE = 0x839A00               # probe record: u32 hit, lr, sp, r1, then 0x200 bytes of stack

# Debug probes: (name, patched address, original word, branch to the probe, probe address, code).
# null_alloc: the small-block allocator (0x6F1C9C, r0 = allocator, r1 = size) called with a
# NULL allocator records its caller once (lr, sp, size, stack) at TRACE, then runs on.
PROBES = {
    "null_alloc": (0x6F1C9C, bytes.fromhex("000051e3"), bytes.fromhex("57cc00ea"), 0x724E00, bytes.fromhex(
        "000050e31200001a1c002de948209fe5003092e5000053e30b00001a04e082e508d082e50c1082e50030a0e3"
        "104082e203009de7030084e7043083e2020c53e3faffffba0130a0e3003082e51c00bde80000a0e3000051e3"
        "9033ffea009a8300")),
    # heap_track: the general heap allocator (0x55E928, r0 = heap, r1 = size) records every
    # heap it is called for at HEAPS (16 x [heap, largest request]), so tests can walk the
    # heaps' free lists over RPC (emutest/heaps.py).
    "heap_track": (0x55E928, bytes.fromhex("70402de9"), bytes.fromhex("f41807ea"), 0x724D00, bytes.fromhex(
        "0c102de944209fe50030a0e3100053e30b0000aa83c192e700005ce10400000a00005ce30100000a013083e2"
        "f6ffffea830182e783c182e004309ce5030051e104108c850c10bde870402de9f6e6f8ea009d8300")),
    # oom: the small-block allocator's fallback (0x6F1CFC: alloc from the general heap at
    # allocator+0x54). The probe counts failures at OOM and keeps the caller (lr) and the size of
    # the last one: OOM = [count, lr, size, -, last fallback size]. It also profiles every
    # fallback allocation by its caller: PROF = 32 x [caller lr, total bytes, count].
    "oom": (0x6F1CFC, bytes.fromhex("540084e2"), bytes.fromhex("bfcb00ea"), 0x724C00, bytes.fromhex(
        "66002de990309fe514509de50060a0e3200056e30e0000aa000093e5050050e10500000a000050e30200000a"
        "0c3083e2016086e2f5ffffea005083e5040093e5010080e0040083e5080093e5010080e2080083e56600bde8"
        "38309fe5101083e5540084e22fe7f8eb000050e31080bd1820309fe5001093e5011081e2001083e504109de5"
        "041083e5101093e5081083e50000a0e31080bde8809c8300809d8300")),
    # heap_live (use with heap_track): live bytes and high-water mark per tracked heap, kept on
    # the general heap's success exit (0x55EA44) and free entry (0x55E808):
    # LIVE = 16 x [live bytes, peak bytes] (same index as HEAPS); block headers included.
    "heap_live_alloc": (0x55EA44, bytes.fromhex("7080bde8"), bytes.fromhex("c51807ea"), 0x724D60, bytes.fromhex(
        "0f002de950209fe50030a0e3100053e30f0000aa831192e7050051e10100000a013083e2f8ffffea101094e5"
        "0211c1e31c1081e224209fe5832182e0000092e5010080e0000082e5041092e5010050e1040082850f00bde8"
        "7080bde8009d830000998300")),
    "heap_live_free": (0x55E808, bytes.fromhex("000051e3"), bytes.fromhex("941907ea"), 0x724E60, bytes.fromhex(
        "000051e31300000a0f002de94c209fe50030a0e3100053e30d0000aa83c192e700005ce10100000a013083e2"
        "f8ffffea08c011e510c09ce502c1cce31cc08ce21c209fe5832182e0000092e50c0040e0000082e50f00bde8"
        "000051e352e6f8ea009d830000998300")),
}
HEAP_LIVE = ("heap_track", "heap_live_alloc", "heap_live_free")
HEAPS = 0x839D00
OOM = 0x839C80
PROF = 0x839D80
LIVE = 0x839900


def apply(code: bytes, probes: tuple = ()) -> bytes:
    out = bytearray(code)
    r, h = RETURN_AT - BASE, HOOK - BASE
    if out[r:r + 4] != ORIGINAL:
        raise ValueError("test hook: PadReader::Read not found at the expected address")
    if any(out[h:h + len(CODE)]):
        raise ValueError("test hook: code cave is in use (moved strings?)")
    out[h:h + len(CODE)] = CODE
    out[r:r + 4] = CALL
    for name in probes:
        at, orig, branch, cave, body = PROBES[name]
        a, c = at - BASE, cave - BASE
        if out[a:a + 4] != orig or any(out[c:c + len(body)]):
            raise ValueError(f"test probe {name}: unexpected code")
        out[c:c + len(body)] = body
        out[a:a + 4] = branch
    return bytes(out)
