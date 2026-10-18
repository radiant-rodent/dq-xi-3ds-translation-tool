"""English grammar for text the game puts together at runtime (articles, plurals).

The PC release builds its battle messages with grammar macros (<LM_DefSgl_Target> "the slime",
<LM_IfSing_Damage(point,points)> ...); the 3DS script is Japanese and has none. The exporter
keeps them as grammar markers (textcodec.GRAMMAR, private-use characters) and a code patch
resolves them in the finished message, after the game has put the names and numbers in:

  {the} {The} {a} {A}  before a name: an article when the name is a common noun (starts with
                       the {noun} flag), which is then written in lower case ("the slime",
                       "An iron turtle"); nothing before proper names (party, bosses).
  {pl}                 after a name: plural ending ("slimes", "drackies", "foxes").
  {n:one|more}         'one' when the nearest number (before it, else after it) is exactly 1.
  {noun}               flag at the start of common-noun names (monster and item tables),
                       zero-width in every font, removed in messages; a flagged name
                       mid-sentence is written in lower case.
  {grp}                after a monster group name (the game appends the plural suffix
                       "{pl}{grp}" to a name when several of a kind are meant): the text is
                       plural from here on; removed.
  {s:one|more}         'more' after a {grp}, else 'one' ("The slimes are defeated.").

resolve() is the reference for the ARM code in the patch (same steps, same order).
"""
from __future__ import annotations

from .textcodec import GRAMMAR, N_END, N_MID, N_START, S_START

THE, THE_CAP, A, A_CAP, PL, NOUN = (GRAMMAR[k] for k in ("the", "The", "a", "A", "pl", "noun"))
GRP = GRAMMAR["grp"]
ARTICLES = {THE: "the ", THE_CAP: "The ", A: "a ", A_CAP: "A "}
VOWELS = "aeiouAEIOU"
SENTENCE_END = ".!?\n\r"


def _number_near(s: list[str], i: int) -> str | None:
    j = i - 1
    while j >= 0 and not s[j].isdigit():
        j -= 1
    if j >= 0:
        k = j
        while k >= 0 and s[k].isdigit():
            k -= 1
        return "".join(s[k + 1:j + 1])
    j = i
    while j < len(s) and not s[j].isdigit():
        j += 1
    if j < len(s):
        k = j
        while k < len(s) and s[k].isdigit():
            k += 1
        return "".join(s[j:k])
    return None


def _sentence_start(s: list[str], i: int) -> bool:
    j = i - 1
    while j >= 0 and s[j] == " ":
        j -= 1
    return j < 0 or s[j] in SENTENCE_END


def resolve(text: str, cap: int = 255) -> str:
    """The finished message with every grammar marker resolved (at most cap characters)."""
    s = list(text[:cap])
    i = 0
    plural = False
    while i < len(s):
        del s[cap:]                 # the game's buffer: growth past it is cut off right away
        if i >= len(s):
            break
        c = s[i]
        if c in ARTICLES:
            if i + 1 < len(s) and s[i + 1] == NOUN:
                art = ARTICLES[c]
                if c in (A, A_CAP) and i + 2 < len(s) and s[i + 2] in VOWELS:
                    art = art[0] + "n "
                s[i:i + 2] = list(art)
                i += len(art)
                if i < len(s) and "A" <= s[i] <= "Z":
                    s[i] = s[i].lower()
            else:
                del s[i]
            continue
        if c == NOUN:
            del s[i]
            if i < len(s) and "A" <= s[i] <= "Z" and not _sentence_start(s, i):
                s[i] = s[i].lower()
            continue
        if c == PL:
            p1 = s[i - 1] if i >= 1 else ""
            p2 = s[i - 2] if i >= 2 else ""
            if p1 and (p1 in "sxzSXZ" or (p1 in "hH" and p2 and p2 in "csCS")):
                s[i:i + 1] = list("es")
            elif p1 and p1 in "yY" and p2 and p2 not in VOWELS:
                s[i - 1:i + 1] = list("ies")
                i += 1
            else:
                s[i] = "s"
            i += 1
            continue
        if c == GRP:
            plural = True
            del s[i]
            continue
        if c in (N_START, S_START):
            try:
                mid = s.index(N_MID, i)
                end = s.index(N_END, mid)
            except ValueError:
                del s[i]
                continue
            one = (not plural) if c == S_START else _number_near(s, i) == "1"
            if one:
                del s[mid:end + 1]
                del s[i]
            else:
                del s[end]
                del s[i:mid + 1]
            continue
        i += 1
    return "".join(s[:cap])


def patch(code: bytes) -> tuple[bytes, list[str]]:
    """The grammar pass (grammar_code, assembled from grammar_asm) in code; (code, problems)."""
    from . import grammar_code as gc
    base = 0x100000
    out = bytearray(code)
    c = gc.CAVE - base
    done = out[c:c + len(gc.CODE)] == gc.CODE
    if not done and any(out[c:c + len(gc.CODE)]):
        return bytes(out), [f"grammar: code cave at {gc.CAVE:#x} is not free"]
    for at, orig, bl in gc.HOOKS:
        h = at - base
        if out[h:h + 4] not in (orig, bl):
            return bytes(code), [f"grammar: unexpected instruction at {at:#x} (different game version?)"]
    from . import namefix                         # pane_fix calls it: always installed with it
    named, probs = namefix.patch(bytes(out), {})
    if probs:
        return bytes(code), probs
    out = bytearray(named)
    out[c:c + len(gc.CODE)] = gc.CODE
    for at, orig, bl in gc.HOOKS:
        out[at - base:at - base + 4] = bl
    return bytes(out), []
