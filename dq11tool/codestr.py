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
    for o in sorted(refset):
        if o & 1:
            continue
        if o >= 2:
            prev = struct.unpack_from("<H", code, o - 2)[0]
            if 0x20 <= prev and code[o - 1] != 0:  # inside another UTF-16 string
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
        out.append(CodeString(o, s, cap_end - o))
    return out


def patch(code: bytes, repl: dict[int, tuple[str, int]]) -> bytes:
    """repl: offset -> (raw string, capacity). Raises if a string does not fit."""
    out = bytearray(code)
    for off, (raw, cap) in repl.items():
        b = raw.encode("utf-16-le") + b"\0\0"
        if len(b) > cap:
            raise ValueError(f"code string at {off:#x} too long ({len(b)} > {cap} bytes)")
        out[off:off + cap] = b + b"\0" * (cap - len(b))
    return bytes(out)
