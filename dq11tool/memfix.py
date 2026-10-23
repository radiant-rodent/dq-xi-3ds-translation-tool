"""Give the UI more memory: move part of an underused heap to the layout heap.

At boot the game carves the linear heap into fixed heaps one after another (0x102EF8): the
main heap (0x2C00000 bytes), then 0xA60000, 0x300000, 0x1C0000, 0x40000 and 0x28000 byte
heaps, then two small-block allocators, each with five block pools and a fallback heap for
everything the pools cannot serve:
  allocator 1 (object 0x7F4F?? + pools): fallback heap 0x1DC340 bytes, literal at 0x103534
  allocator 2 (object 0x7F50F0, alloc 0x6F1C9C): fallback heap 0x23F880 bytes, literal at
      0x103540, the last heap; it ends at the end of the linear heap
The base of allocator 2 is computed from where allocator 1 ends.

Allocator 2 serves the UI layouts: every text box takes a vertex buffer of ~312 bytes per
character from it (0x53F344), and English text is longer: the translation needs ~470 KB more
of this heap at its peak (measured with emutest/memreport.py: 2.31 MB of 2.36 MB, against
1.84 MB in Japanese), so the item details screens ran it out of memory. On failure the
allocator does not return NULL but the address of the heap object itself (0x6F1D08
`addeq r0, r4, #0x54`): the next layout object is built on top of the heap's bookkeeping
and the game hangs in the allocator (the "item menu" / "shop" crash).

patch() takes `amount` bytes from allocator 1's fallback heap (peak use ~350 KB of 1.95 MB in
every scenario measured) and gives them to allocator 2's: allocator 2 moves down by `amount`
and ends where it always did; nothing else changes. Works the same on Old and New 3DS.
"""
from __future__ import annotations

import struct

BASE = 0x100000
DONOR_LITERAL = 0x103534     # .word 0x1DC340: fallback heap of small-block allocator 1
UI_HEAP_LITERAL = 0x103540   # .word 0x23F880: fallback heap of allocator 2 (UI layouts)
DONOR_SIZE = 0x1DC340
UI_SIZE = 0x23F880
MAX_AMOUNT = 0x140000        # leaves allocator 1 ~650 KB (peak use measured ~350 KB)


OOM_RETURN_BUG = 0x6F1D08    # addeq r0, r4, #0x54: stripped debug call left r0 = &fallback_heap on OOM
OOM_BUG_ORIG = 0x02840054
ARM_NOP = 0xE1A00000


def patch(code: bytes, amount: int) -> bytes:
    """Move `amount` bytes (multiple of 64 KB) from allocator 1's fallback heap to the UI heap,
    and fix 0x6F1D08 so fallback OOM returns NULL instead of &this->fallback_heap."""
    if amount <= 0:
        return code
    if amount % 0x10000 or amount > MAX_AMOUNT:
        raise ValueError(f"UI heap extra must be a multiple of 64 KB up to {MAX_AMOUNT:#x}")
    out = bytearray(code)
    donor, ui = (struct.unpack_from("<I", out, a - BASE)[0] for a in (DONOR_LITERAL, UI_HEAP_LITERAL))
    if donor != DONOR_SIZE or ui != UI_SIZE:
        raise ValueError("heap setup literals not found (different game version?)")
    struct.pack_into("<I", out, DONOR_LITERAL - BASE, DONOR_SIZE - amount)
    struct.pack_into("<I", out, UI_HEAP_LITERAL - BASE, UI_SIZE + amount)
    if struct.unpack_from("<I", out, OOM_RETURN_BUG - BASE)[0] == OOM_BUG_ORIG:
        struct.pack_into("<I", out, OOM_RETURN_BUG - BASE, ARM_NOP)
    return bytes(out)


def reserve_tail(code: bytes, amount: int) -> bytes:
    """Tests only: take `amount` bytes from allocator 1's fallback heap and give them to nobody.
    Allocator 2 (UI) keeps its size but starts and ends `amount` lower, so the last `amount`
    bytes of the linear heap stay unused (textlog's ring buffer; emutest/textrec.py finds them
    after allocator 2's heap)."""
    if amount % 0x10000:
        raise ValueError("reserve must be a multiple of 64 KB")
    out = bytearray(code)
    donor = struct.unpack_from("<I", out, DONOR_LITERAL - BASE)[0]
    if not DONOR_SIZE - MAX_AMOUNT <= donor <= DONOR_SIZE or donor - amount < DONOR_SIZE - MAX_AMOUNT - 0x40000:
        raise ValueError("heap setup literal not as expected (reserve too big?)")
    struct.pack_into("<I", out, DONOR_LITERAL - BASE, donor - amount)
    return bytes(out)


# ----------------------------------------------------------------------------- main heap
# The fixed heaps are set up with ARM immediates (0x102EF8): `mov r2, #size` is the size passed
# to the heap constructor, `add r4, r4, #size` advances to the next heap's base.
MAIN_MOV, MAIN_ADD = 0x102F3C, 0x102F54        # main heap 0x2C00000
DONOR3_MOV, DONOR3_ADD = 0x102F7C, 0x102F8C    # third heap 0x300000 (peak use ~1.2 MB)
MAIN_SIZE, DONOR3_SIZE = 0x2C00000, 0x300000
MAX_MAIN = 0x100000                            # leaves the third heap 2 MB


def _imm(value: int) -> int | None:
    """ARM data-processing immediate (rotate:4 | imm8) for value, or None."""
    for rot in range(16):
        v = ((value << (2 * rot)) | (value >> (32 - 2 * rot))) & 0xFFFFFFFF if rot else value
        if v < 0x100:
            return (rot << 8) | v
    return None


def _set_imm(out: bytearray, addr: int, old: int, new: int):
    word = struct.unpack_from("<I", out, addr - BASE)[0]
    if _imm(old) is None or word & 0xFFF != _imm(old):
        raise ValueError(f"heap setup instruction at {addr:#x} not found (different game version?)")
    struct.pack_into("<I", out, addr - BASE, (word & ~0xFFF) | _imm(new))


def patch_main(code: bytes, amount: int) -> bytes:
    """Move `amount` bytes (multiple of 1 MB) from the third fixed heap to the main heap: the
    main heap grows, the 0xA60000 heap after it moves up, the third heap shrinks and ends where
    it always did. Measured 2026-10-13 in Cobblestone with the restored official NPC lines:
    main heap peak 45.3 MB of 46.1 MB, third heap peak 1.2 MB of 3 MB."""
    if amount <= 0:
        return code
    if amount % 0x100000 or amount > MAX_MAIN:
        raise ValueError(f"main heap extra must be a multiple of 1 MB up to {MAX_MAIN:#x}")
    out = bytearray(code)
    for addr, old, new in ((MAIN_MOV, MAIN_SIZE, MAIN_SIZE + amount), (MAIN_ADD, MAIN_SIZE, MAIN_SIZE + amount),
                           (DONOR3_MOV, DONOR3_SIZE, DONOR3_SIZE - amount),
                           (DONOR3_ADD, DONOR3_SIZE, DONOR3_SIZE - amount)):
        _set_imm(out, addr, old, new)
    return bytes(out)
