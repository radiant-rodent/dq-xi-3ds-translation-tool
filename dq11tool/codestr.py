"""Hardcoded UTF-16 strings in the ExeFS .code binary.

Strings are found from their references: absolute addresses (0x100000 + offset) in
literal pools, and ARM `ADR` instructions (ADD/SUB Rd, PC, #imm) for strings placed
in literal pools inside .text. Strings can only be edited in place: a replacement must
fit in the original string's bytes plus alignment padding.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .bxon import valid_text
from .textcodec import decode_str

BASE = 0x100000


@dataclass
class CodeString:
    offset: int
    raw: str
    capacity: int  # bytes available incl. NUL terminator

    @property
    def text(self) -> str:
        return decode_str(self.raw)


def _ror(v: int, r: int) -> int:
    return ((v >> r) | (v << (32 - r))) & 0xFFFFFFFF if r else v


def references(code: bytes, text_size: int | None = None) -> set[int]:
    n = len(code)
    words = struct.unpack_from(f"<{n // 4}I", code, 0)
    refs = {w - BASE for w in words if BASE <= w < BASE + n and not (w & 1)}
    lim = (text_size or n) // 4
    for i in range(lim):
        w = words[i]
        op = w & 0x0FFF0000
        if op == 0x028F0000 or op == 0x024F0000:      # ADD/SUB Rd, PC, #imm
            imm = _ror(w & 0xFF, ((w >> 8) & 0xF) * 2)
            t = i * 4 + 8 + (imm if op == 0x028F0000 else -imm)
            if 0 <= t < n:
                refs.add(t)
    return refs


def _is_kana(o: int) -> bool:
    return 0x3041 <= o <= 0x30FA and not (0x3030 <= o <= 0x303F)


def looks_japanese(s: str) -> bool:
    """Stricter than valid_text: rejects ASCII / pointer data misread as UTF-16."""
    if len(s) < 2:
        return False
    kana = kanji = ascii_pairs = ascii = 0
    for ch in s:
        o = ord(ch)
        if 0x3030 <= o <= 0x303F or o >= 0xE000 and o < 0xF900 or (o < 0x20 and ch != "\n"):
            return False
        if o < 0x80:
            ascii += 1
        if _is_kana(o):
            kana += 1
        elif 0x4E00 <= o < 0xA000:
            kanji += 1
            lo, hi = o & 0xFF, o >> 8
            if 0x20 <= lo < 0x7F and 0x20 <= hi < 0x7F:
                ascii_pairs += 1
    if kana == 0 and (kanji < 2 or ascii):
        return False
    if kanji and ascii_pairs * 2 > kanji:
        return False
    if "あいうえおかきくけこ" in s:   # kana keyboard tables for name entry
        return False
    return True


def is_debug(s: str) -> bool:
    """printf-style developer log lines that never appear on screen."""
    return s.endswith("\n") and any(c.isascii() and (c.isalpha() or c in ":[]") for c in s) \
        or s.startswith(("AI", "追加効果", "[Error]", "/////")) or "テスト" in s or "でばっぐ" in s \
        or "未設定" in s or "不正な" in s


def find_strings(code: bytes, text_size: int | None = None) -> list[CodeString]:
    n = len(code)
    refset = references(code, text_size)
    out = []
    spans: list[tuple[int, int]] = []
    for o in sorted(refset):
        if o & 1:
            continue
        if any(s_start <= o < s_end for s_start, s_end in spans):
            continue
        end = o
        while end + 1 < n and code[end:end + 2] != b"\0\0":
            end += 2
            if end - o > 512:
                break
        if end - o < 4 or end - o > 512:
            continue
        try:
            s = code[o:end].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if not valid_text(s) or not looks_japanese(s):
            continue
        term = end + 2
        cap_end = (term + 3) & ~3
        if any(code[i] for i in range(term, min(cap_end, n))) or any(r in refset for r in range(o + 1, cap_end)):
            cap_end = term
        spans.append((o, cap_end))
        out.append(CodeString(o, s, cap_end - o))
    return out


# The padding after .text (0x72403C..0x725000) is kept for code caves, so moved strings go
# into the .rodata padding only: 0x724040 autofit.CAVE, 0x724380 grammar.CAVE, 0x724A00-0x724BBC
# wordwrap, test builds: 0x724C00 textlog.CAVE or testhook probes, 0x724F00 testhook
RESERVED_TEXT_TAIL = 0x62403C


def free_space(code: bytes, exheader: bytes) -> list[list[int]]:
    """Unused zero padding after the text and rodata segments: [start, end) file offsets.
    Both are mapped readable, so moved strings can live there."""
    out, off = [], 0
    for i in range(3):
        size, pages = struct.unpack_from("<I", exheader, 0x18 + i * 0x10)[0], \
            struct.unpack_from("<I", exheader, 0x14 + i * 0x10)[0]
        start, end = (off + size + 3) & ~3, min(off + pages * 0x1000, len(code))
        if i == 0:
            end = min(end, RESERVED_TEXT_TAIL)      # nothing: the text tail holds code caves
        if i < 2 and start < end and not any(code[start:end]):   # not data: bss follows it
            out.append([start, end])
        off += pages * 0x1000
    return out


def _adr_refs(code: bytes, text_size: int) -> dict[int, list[int]]:
    """target offset -> offsets of ARM `ADD/SUB Rd, PC, #imm` instructions that compute it."""
    words = struct.unpack_from(f"<{text_size // 4}I", code, 0)
    out: dict[int, list[int]] = {}
    for i, w in enumerate(words):
        op = w & 0x0FFF0000
        if op == 0x028F0000 or op == 0x024F0000:
            imm = _ror(w & 0xFF, ((w >> 8) & 0xF) * 2)
            out.setdefault(i * 4 + 8 + (imm if op == 0x028F0000 else -imm), []).append(i * 4)
    return out


def _ptr_refs(code: bytes) -> dict[int, list[int]]:
    """target offset -> offsets of u32 words holding its absolute address."""
    n = len(code)
    words = struct.unpack_from(f"<{n // 4}I", code, 0)
    out: dict[int, list[int]] = {}
    for i, w in enumerate(words):
        if BASE <= w < BASE + n and not w & 1:
            out.setdefault(w - BASE, []).append(i * 4)
    return out


class Mover:
    """Strings too long for their slot are moved into free space. Pointers to them are
    re-pointed, and each `ADR Rd, string` becomes `LDR Rd, [PC, #x]` that loads the new
    address from a pointer stored in the old slot (always within LDR's 4 KB reach)."""

    def __init__(self, code: bytes, exheader: bytes):
        self.code = code
        self.text_size = struct.unpack_from("<I", exheader, 0x18)[0]
        self.adr = _adr_refs(code, self.text_size)
        self.ptr = _ptr_refs(code)
        self.free = free_space(code, exheader)

    def _slot(self, loc: int, cap: int) -> int | None:
        """Where the new pointer goes inside the old slot (4-aligned, empty string before it)."""
        p = (loc + 3) & ~3
        if p == loc and cap >= 8:
            p += 4                       # keep a NUL first, so a reader we missed sees ""
        return p if p + 4 <= loc + cap else None

    def why_not(self, loc: int, cap: int) -> str | None:
        adrs, ptrs = self.adr.get(loc, []), self.ptr.get(loc, [])
        if not adrs and not ptrs:
            return "no reference to it found"
        if adrs:
            p = self._slot(loc, cap)
            if p is None:
                return "slot too small for a pointer"
            for a in adrs:
                w = struct.unpack_from("<I", self.code, a)[0]
                if (w >> 12) & 0xF == 15 or abs(p - (a + 8)) >= 4096:
                    return "unsupported instruction"
        # a string followed by an unreferenced one is probably the head of a table the
        # code indexes into; moving the head would break the other entries
        nxt = loc + cap
        if nxt not in self.adr and nxt not in self.ptr and nxt + 2 < len(self.code):
            end = nxt
            while end + 1 < len(self.code) and self.code[end:end + 2] != b"\0\0" and end - nxt < 128:
                end += 2
            try:
                s = self.code[nxt:end].decode("utf-16-le")
            except UnicodeDecodeError:
                s = ""
            if valid_text(s) and looks_japanese(s):   # literal-pool words often decode as one kanji
                return "part of a table"
        return None

    def apply(self, code: bytes, moves: dict[int, tuple[str, int]]) -> tuple[bytes, dict[int, str]]:
        """moves: offset -> (raw string, slot capacity). Returns (code, {offset: reason not moved})."""
        out = bytearray(code)
        free = [list(f) for f in self.free]
        failed, placed = {}, {}
        for loc, (raw, cap) in sorted(moves.items(), key=lambda m: -len(m[1][0])):
            why = self.why_not(loc, cap)
            if why:
                failed[loc] = why
                continue
            b = raw.encode("utf-16-le") + b"\0\0"
            new = placed.get(b)
            if new is None:
                f = next((f for f in free if f[1] - f[0] >= len(b)), None)
                if f is None:
                    failed[loc] = "no free space left in the executable"
                    continue
                new = f[0]
                out[new:new + len(b)] = b
                f[0] = (new + len(b) + 3) & ~3
                placed[b] = new
            for r in self.ptr.get(loc, []):
                struct.pack_into("<I", out, r, BASE + new)
            adrs = self.adr.get(loc, [])
            if adrs:
                p = self._slot(loc, cap)
                out[loc:loc + cap] = b"\0" * cap
                struct.pack_into("<I", out, p, BASE + new)
                for a in adrs:
                    w = struct.unpack_from("<I", out, a)[0]
                    d = p - (a + 8)
                    ldr = (w & 0xF000F000) | (0x059F0000 if d >= 0 else 0x051F0000) | abs(d)
                    struct.pack_into("<I", out, a, ldr)
        return bytes(out), failed


def patch(code: bytes, repl: dict[int, tuple[str, int]]) -> bytes:
    """repl: offset -> (raw string, capacity). Raises if a string does not fit."""
    out = bytearray(code)
    for off, (raw, cap) in repl.items():
        b = raw.encode("utf-16-le") + b"\0\0"
        if len(b) > cap:
            raise ValueError(f"code string at {off:#x} too long ({len(b)} > {cap} bytes)")
        out[off:off + cap] = b + b"\0" * (cap - len(b))
    return bytes(out)
