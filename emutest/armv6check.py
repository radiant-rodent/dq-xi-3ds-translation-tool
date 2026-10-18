"""armv6check.py ORIGINAL_CODE PATCHED_CODE - changed words that are ARMv7 / VFPv3-only encodings.
The 3DS CPU is an ARM11 MPCore (ARMv6K, VFPv2); Azahar also runs newer instructions, so a patch
can work in the emulator and crash a real console with "undefined instruction" (2026-10-17:
autofit used vmov.f32 s16, #1.0 and the X menu crashed on a 2DS). ldrd hits are usually data
(literal pools), check the address. Run after changing any code cave."""
import struct, sys
orig = open(sys.argv[1], "rb").read()
new = open(sys.argv[2], "rb").read()
PATS = [  # (name, mask, value) ARM (cond != 0xF) encodings not in ARMv6K / VFPv2
    ("vmov.f32/f64 #imm (VFPv3)", 0x0FB00EF0, 0x0EB00A00),
    ("movw (v6T2)", 0x0FF00000, 0x03000000),
    ("movt (v6T2)", 0x0FF00000, 0x03400000),
    ("ubfx/sbfx (v6T2)", 0x0FA00070, 0x07A00050),
    ("bfi/bfc (v6T2)", 0x0FE00070, 0x07C00010),
    ("sdiv/udiv (v7VE)", 0x0FD0F0F0, 0x0710F010),
    ("mls (v6T2)", 0x0FF000F0, 0x00600090),
    ("rbit (v6T2)", 0x0FFF0FF0, 0x06FF0F30),
    ("ldrd/strd w/ T (v6T2)", 0x0F2000F0, 0x002000B0),
    ("vcvt fixed (VFPv3)", 0x0FBA0E50, 0x0EBA0A40),
]
n = min(len(orig), len(new))
hits = []
for o in range(0, len(new) - 3, 4):
    w = struct.unpack_from("<I", new, o)[0]
    if o < n and struct.unpack_from("<I", orig, o)[0] == w:
        continue      # unchanged word
    if (w >> 28) == 0xF:
        continue
    for name, m, v in PATS:
        if w & m == v:
            hits.append((hex(0x100000 + o), hex(w), name))
for h in hits: print(*h)
print(len(hits), "suspicious changed words")
