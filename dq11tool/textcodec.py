"""Conversion between the game's raw strings and an editable tagged form.

Control codes are rendered as readable tags so translators can see/keep them:
  U+0001 base U+0002 reading U+0003  -> {ruby:base|reading}   (furigana)
  U+0004 X                           -> {v:XX}                (runtime variable, X as hex)
  U+000D                             -> {CR}                  (before a newline: page/paragraph break)
  U+001F                             -> {PAGE}                (tool-only: split an NPC talk line into
                                                               two message boxes, see ietalk.py)
  U+E000..U+E007                     -> {the} {The} {a} {A} {pl} {n:singular|plural}
                                        (tool-only grammar markers, resolved in game by
                                        grammar.py's pass over the finished message)
  U+E008                             -> {noun} (first character of a common-noun name: takes
                                        articles, lowercase in sentences; zero-width glyph)
  U+E009, U+E00A ... U+E006 ... U+E007 -> {grp} (after a monster group name: plural) and
                                        {s:singular|plural} (verb by that: "is" / "are")
  other C0 controls                  -> {cXX}
  literal braces                     -> {{ and }}
"""
from __future__ import annotations

import re

RUBY_START, RUBY_MID, RUBY_END, VAR = "\x01", "\x02", "\x03", "\x04"
PAGE = "\x1f"       # unused by every bxon string of the game (checked 2026-10-13)
# grammar markers (private use area: no game string uses U+E000-U+F8FF, checked 2026-10-13)
GRAMMAR = {"the": "", "The": "", "a": "", "A": "", "pl": "",
           "noun": ""}
N_START, N_MID, N_END = "", "", ""
GRAMMAR["grp"] = ""        # after a group name ("slimes"): the sentence is plural
S_START = ""               # {s:singular|plural}: by the nearest name ({grp} = plural)
_GRAMMAR_TAG = {v: k for k, v in GRAMMAR.items()}
_N_TAG = {N_START: "{n:", S_START: "{s:", N_MID: "|", N_END: "}"}


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
        elif ch == PAGE:
            out.append("{PAGE}")
        elif ch in _GRAMMAR_TAG:
            out.append("{" + _GRAMMAR_TAG[ch] + "}")
        elif ch in _N_TAG:
            out.append(_N_TAG[ch])
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


_TAG = re.compile(r"\{(ruby:[^|{}]*\|[^{}]*|v:[0-9A-Fa-f]+|CR|PAGE|the|The|a|A|pl|noun|grp|c[0-9A-Fa-f]{2})\}")


def encode_str(text: str) -> str:
    out = []
    i = 0
    text = text.replace("\r\n", "\n")
    n = len(text)
    in_n = 0                     # 1 inside {n:..|, 2 after its |
    while i < n:
        if text.startswith("{{", i):
            out.append("{")
            i += 2
            continue
        if text.startswith("}}", i):
            out.append("}")
            i += 2
            continue
        if not in_n and (text.startswith("{n:", i) or text.startswith("{s:", i)):
            out.append(N_START if text[i + 1] == "n" else S_START)
            in_n, i = 1, i + 3
            continue
        if in_n == 1 and text[i] == "|":
            out.append(N_MID)
            in_n, i = 2, i + 1
            continue
        if in_n == 2 and text[i] == "}":
            out.append(N_END)
            in_n, i = 0, i + 1
            continue
        m = _TAG.match(text, i)
        if m:
            tag = m.group(1)
            if tag in GRAMMAR:
                out.append(GRAMMAR[tag])
            elif tag.startswith("ruby:"):
                base, reading = tag[5:].split("|", 1)
                out.append(RUBY_START + base + RUBY_MID + reading + RUBY_END)
            elif tag.startswith("v:"):
                out.append(VAR + chr(int(tag[2:], 16)))
            elif tag == "CR":
                out.append("\r")
            elif tag == "PAGE":
                out.append(PAGE)
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
    return [m.group(0) for m in _TAG.finditer(text) if not m.group(1).startswith("ruby:")
            and m.group(1) not in ("CR", "PAGE") and m.group(1) not in GRAMMAR]


_N_BLOCK = re.compile(r"\{[ns]:((?:[^{}|]|\{[^{}]*\})*)\|((?:[^{}]|\{[^{}]*\})*)\}")


def resolve_grammar(text: str, longest: bool = True) -> str:
    """Grammar tags out, for measuring: articles as 'the '/'an ' (the wider case), {n:a|b} and
    {s:a|b} as the longer branch (or the plural one), {pl} as 'es'."""
    text = _N_BLOCK.sub(lambda m: max(m.groups(), key=len) if longest else m.group(2), text)
    for tag, rep in (("{the}", "the "), ("{The}", "The "), ("{a}", "an "), ("{A}", "An "),
                     ("{pl}", "es"), ("{noun}", ""), ("{grp}", "")):
        text = text.replace(tag, rep if longest else "")
    return text


def strip_tags(text: str) -> str:
    """Plain reading of a decoded string (ruby collapsed to base text, tags removed)."""
    text = resolve_grammar(text)
    text = re.sub(r"\{ruby:([^|{}]*)\|[^{}]*\}", r"\1", text)
    text = text.replace("{CR}", "").replace("{PAGE}", " ")
    text = re.sub(r"\{v:[0-9A-Fa-f]+\}", "＊", text)
    text = re.sub(r"\{c[0-9A-Fa-f]{2}\}", "", text)
    return text.replace("{{", "{").replace("}}", "}")


def has_japanese(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o < 0x3100 or 0x4E00 <= o < 0xA000 or 0xFF66 <= o < 0xFFA0:
            return True
    return False


# Ranges that mark Japanese on-screen text even without any kana/kanji: CJK punctuation
# (「」、。・ー) and dashes/quotes/ellipsis (… ―). NPC/party lines that are only
# punctuation (e.g. "「……………………。") contain no kana or kanji yet are real dialogue.
_DISPLAY_PUNCT = ((0x2010, 0x2030), (0x3000, 0x3040))


def is_display_text(text: str) -> bool:
    """True if text is meant to be shown to the player, so it is worth extracting.

    Includes kana/kanji (see has_japanese) but also Japanese punctuation and dashes/
    ellipsis, so a line such as "「……………………。" is not dropped just because it has no
    kana or kanji.
    """
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o < 0x3100 or 0x4E00 <= o < 0xA000 or 0xFF66 <= o < 0xFFA0:
            return True
        for a, b in _DISPLAY_PUNCT:
            if a <= o < b:
                return True
    return False
