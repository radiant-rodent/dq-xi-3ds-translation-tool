"""Conversion between the game's raw strings and an editable tagged form.

Control codes are rendered as readable tags so translators can see/keep them:
  U+0001 base U+0002 reading U+0003  -> {ruby:base|reading}   (furigana)
  U+0004 X                           -> {v:XX}                (runtime variable, X as hex)
  U+000D                             -> {CR}                  (before a newline: page/paragraph break)
  other C0 controls                  -> {cXX}
  literal braces                     -> {{ and }}
"""
from __future__ import annotations

import re

RUBY_START, RUBY_MID, RUBY_END, VAR = "\x01", "\x02", "\x03", "\x04"


def decode_str(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        o = ord(ch)
        if ch == RUBY_START:
            j = s.find(RUBY_MID, i + 1)
            k = s.find(RUBY_END, j + 1) if j != -1 else -1
            if j != -1 and k != -1:
                out.append("{ruby:" + s[i + 1:j] + "|" + s[j + 1:k] + "}")
                i = k + 1
                continue
        if ch == VAR and i + 1 < n:
            out.append("{v:%X}" % ord(s[i + 1]))
            i += 2
            continue
        if ch == "\n":
            out.append("\n")
        elif ch == "\r":
            out.append("{CR}")
        elif ch == "{":
            out.append("{{")
        elif ch == "}":
            out.append("}}")
        elif o < 0x20:
            out.append("{c%02X}" % o)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def decode_utf16(raw: bytes) -> str:
    return decode_str(raw.decode("utf-16-le", errors="surrogatepass"))


_TAG = re.compile(r"\{(ruby:[^|{}]*\|[^{}]*|v:[0-9A-Fa-f]+|CR|c[0-9A-Fa-f]{2})\}")


def encode_str(text: str) -> str:
    out = []
    i = 0
    text = text.replace("\r\n", "\n")
    n = len(text)
    while i < n:
        if text.startswith("{{", i):
            out.append("{")
            i += 2
            continue
        if text.startswith("}}", i):
            out.append("}")
            i += 2
            continue
        m = _TAG.match(text, i)
        if m:
            tag = m.group(1)
            if tag.startswith("ruby:"):
                base, reading = tag[5:].split("|", 1)
                out.append(RUBY_START + base + RUBY_MID + reading + RUBY_END)
            elif tag.startswith("v:"):
                out.append(VAR + chr(int(tag[2:], 16)))
            elif tag == "CR":
                out.append("\r")
            else:
                out.append(chr(int(tag[1:], 16)))
            i = m.end()
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def encode_utf16(text: str) -> bytes:
    return encode_str(text).encode("utf-16-le", errors="surrogatepass")


def tags_of(text: str) -> list[str]:
    """Variable/control tags in a decoded string (ruby excluded) - used to check translations."""
    return [m.group(0) for m in _TAG.finditer(text) if not m.group(1).startswith("ruby:") and m.group(1) != "CR"]


def strip_tags(text: str) -> str:
    """Plain reading of a decoded string (ruby collapsed to base text, tags removed)."""
    text = re.sub(r"\{ruby:([^|{}]*)\|[^{}]*\}", r"\1", text)
    text = text.replace("{CR}", "")
    text = re.sub(r"\{v:[0-9A-Fa-f]+\}", "＊", text)
    text = re.sub(r"\{c[0-9A-Fa-f]{2}\}", "", text)
    return text.replace("{{", "{").replace("}}", "}")


def has_japanese(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o < 0x3100 or 0x4E00 <= o < 0xA000 or 0xFF66 <= o < 0xFFA0:
            return True
    return False
