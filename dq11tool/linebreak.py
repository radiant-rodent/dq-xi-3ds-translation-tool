"""Better line breaks inside message boxes.

The wrapper that broke most translations fills a line as far as it goes, so a sentence is cut
wherever the line happens to end: "Howdy! I'm a talking horse! I love to chat! Ask / me
anything!" where "Howdy! ... chat! / Ask me anything!" reads far better. This re-breaks a text
inside its message window without changing a word:

  - the number of lines stays the same (a poem or a list with a sentence per line is left alone:
    a break after a sentence end is kept, only the runs of lines between such breaks are
    re-flowed);
  - the lines get the cost of `line_cost` (ragged right edge, break after a sentence end or a
    comma preferred, no break after "the" / "of" / "and"..., no lone short last word) and the
    cheapest way to break the run is chosen by dynamic programming;
  - only a clear improvement is written, and only when the result still fits every window.

Only strings of the talk windows (gamefont paged / talk-only) that are not marked as hand-made
(origin "manual") are touched. {CR} at the end of every line but the last is kept that way.

    python -m dq11tool.linebreak [workspace] [--apply] [--show N]
"""
from __future__ import annotations

import re

from .bubble import _BINDING
from .metrics import avg_char_width, line_widths

MARGIN = 6.0            # a lone last word is only fixed when the new breaks are this much cheaper
RAGGED = 12.0           # a break at a sentence end may leave the lines this much more ragged
_SENT = re.compile(r"[.!?…♪][\"')”]*$")
_COMMA = re.compile(r"[,;:—\-)]$")


def _word(s: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", s.lower())


def _tokens(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for c in text:
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        if c == " " and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += c
    if cur:
        out.append(cur)
    return out


class _Run:
    """Words of one run of lines with the widths needed to cost any way of breaking it."""

    def __init__(self, words: list[str], font, width: float):
        self.words, self.width = words, width
        self.sp = font.width_of(" ")
        self.w = [line_widths(x, font)[0] for x in words]
        self.pre = [0]
        for x in self.w:
            self.pre.append(self.pre[-1] + x)

    def span(self, i: int, j: int) -> float:
        return self.pre[j] - self.pre[i] + self.sp * (j - i - 1)

    def line_cost(self, i: int, j: int, last: bool) -> float:
        w = self.span(i, j)
        if w > self.width:
            return float("inf")
        W = self.width
        if last:
            return 12 * max(0.0, 1 - w / (0.22 * W))        # a lone short last word
        c = 100 * ((W - w) / W) ** 2
        end = self.words[j - 1]
        if _SENT.search(end):
            c -= 12
        elif _COMMA.search(end):
            c -= 5
        elif _word(end) in _BINDING:
            c += 10
        return c

    def score(self, cuts: list[int]) -> int:
        """Where the breaks fall: +2 after a sentence end, +1 after a comma, -2 after a word
        that needs the next one."""
        sc = 0
        for c in cuts:
            end = self.words[c - 1]
            sc += 2 if _SENT.search(end) else 1 if _COMMA.search(end) else -2 if _word(end) in _BINDING else 0
        return sc

    def orphan(self, cuts: list[int]) -> bool:
        """The last line is one short word."""
        i = ([0] + cuts)[-1]
        return len(self.words) - i == 1 and self.span(i, len(self.words)) < 0.15 * self.width

    def unfilled(self, cuts: list[int]) -> bool:
        """A line that breaks early (less than 80% full) with no comma to break at."""
        b = [0] + cuts + [len(self.words)]
        for k in range(len(b) - 2):
            if self.span(b[k], b[k + 1]) < 0.8 * self.width and not _COMMA.search(self.words[b[k + 1] - 1]):
                return True
        return False

    def greedy(self, lines: int) -> list[int] | None:
        """Every line filled as far as it goes; None when that needs more than `lines` lines."""
        cuts, i = [], 0
        while i < len(self.words):
            j = i + 1
            while j < len(self.words) and self.span(i, j + 1) <= self.width:
                j += 1
            if self.span(i, j) > self.width:
                return None
            if j < len(self.words):
                cuts.append(j)
            i = j
        if len(cuts) < lines and cuts and cuts[-1] == len(self.words) - 1 and (
                len(cuts) == 1 or cuts[-1] - cuts[-2] > 3) and cuts[-1] > 3:
            cuts[-1] -= 1                  # a lone last word takes the word before it along
        return cuts if len(cuts) < lines else None

    def cost(self, cuts: list[int]) -> float:
        """cuts: word index where every line but the first starts."""
        b = [0] + cuts + [len(self.words)]
        return sum(self.line_cost(b[k], b[k + 1], k == len(b) - 2) for k in range(len(b) - 1))

    def best(self, lines: int) -> list[int] | None:
        n = len(self.words)
        inf = float("inf")
        f = [[inf] * (n + 1) for _ in range(lines + 1)]
        back = [[0] * (n + 1) for _ in range(lines + 1)]
        f[0][0] = 0.0
        for k in range(1, lines + 1):
            for j in range(k, n + 1):
                for i in range(k - 1, j):
                    if f[k - 1][i] == inf:
                        continue
                    c = f[k - 1][i] + self.line_cost(i, j, k == lines and j == n)
                    if c < f[k][j]:
                        f[k][j], back[k][j] = c, i
        if f[lines][n] == inf:
            return None
        cuts, j = [], n
        for k in range(lines, 0, -1):
            i = back[k][j]
            if k > 1:
                cuts.append(i)
            j = i
        return sorted(cuts)


def _better(r: "_Run", old: list[int], new: list[int]) -> bool:
    """Clearly better: the breaks fall at sentence ends / commas where they did not, or a lone
    short last word is gone; and the lines are not much more ragged than before."""
    gain = r.cost(old) - r.cost(new)
    if r.orphan(new) and not r.orphan(old):
        return False
    if r.score(new) > r.score(old) and gain > -RAGGED:
        return True
    return r.orphan(old) and not r.orphan(new) and r.score(new) >= r.score(old) and gain > MARGIN


def _page_lines(page: str):
    """(lines without their {CR}, cr) for a page whose lines all end alike, else None."""
    lines = page.split("\n")
    ends = [l.rstrip().endswith("{CR}") for l in lines[:-1]]
    if any(ends) and not all(ends):
        return None
    cr = bool(ends) and all(ends)
    out = [re.sub(r"\s*\{CR\}\s*$", "", l).strip() for l in lines]
    if "{CR}" in "".join(out) or not all(out):
        return None
    return out, cr


def improve_page(page: str, font, width: float) -> str:
    got = _page_lines(page)
    if not got or len(got[0]) < 2:
        return page
    lines, cr = got
    # runs: consecutive lines up to a line that ends a sentence
    runs, cur = [], []
    for l in lines:
        cur.append(l)
        if _SENT.search(l):
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        if len(run) >= 2:
            words = _tokens(" ".join(run))
            r = _Run(words, font, width)
            old, i = [], 0
            for l in run[:-1]:
                i += len(_tokens(l))
                old.append(i)
            if len(words) >= 3 and all(1 <= c < len(words) for c in old):
                if any(_SENT.search(x) for x in words[:-1]):
                    new = r.best(len(run))
                    ok = new and new != old and _better(r, old, new)
                else:
                    # one sentence: no sentence end to break at, so fill the lines (the way a
                    # hand-made break looks best: "...so please don't leave me alone / too long.")
                    new = r.greedy(len(run)) if r.unfilled(old) else None
                    ok = new is not None and new != old
                if ok:
                    b = [0] + new + [len(words)]
                    run = [" ".join(words[b[k]:b[k + 1]]) for k in range(len(b) - 1)]
        out += run
    return "\n".join(l + ("{CR}" if cr and k < len(out) - 1 else "") for k, l in enumerate(out))


def improve(text: str, font, width: float) -> str:
    return "{PAGE}".join(improve_page(p, font, width) for p in text.split("{PAGE}"))


def window(gf, sid: int, source: str) -> float:
    """Narrowest line (px of the string's font) over every window the string is shown in."""
    font = gf.font(sid)
    w = gf.get(sid, source)["width"]
    for f, ew in gf._extra(sid):
        w = min(w, ew * avg_char_width(font) / avg_char_width(f))
    return w


def main():
    import argparse
    import os
    import shutil
    import time
    from . import gamefont
    from .project import Project
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace", nargs="?", default="workspace")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=15, help="examples to print")
    a = ap.parse_args()
    project = Project(a.workspace)
    gf = gamefont.for_project(project)
    db = project.db()
    rows = db.execute("SELECT id, text, translation, origin FROM source"
                      " WHERE translation LIKE '%' || char(10) || '%'").fetchall()
    bub = gf.bubbles()
    changes = []
    for r in rows:
        sid = r["id"]
        if r["origin"] == "manual" or sid in bub or not (gf.paged(sid) or gf.talk_only(sid)):
            continue
        if gf.name_field(sid):
            continue
        font = gf.font(sid)
        new = improve(r["translation"], font, window(gf, sid, r["text"]))
        if new != r["translation"] and gf.fits(sid, r["text"], new):
            changes.append((sid, r["translation"], new))
    print(f"{len(rows)} multi-line strings looked at, {len(changes)} get better breaks")
    for sid, old, new in changes[:a.show]:
        print(f"{sid}:\n  {old!r}\n  {new!r}")
    if a.apply and changes:
        bak = os.path.join(project.workdir, f"project.db.before-linebreak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy(os.path.join(project.workdir, "project.db"), bak)
        for sid, old, new in changes:
            db.execute("UPDATE source SET translation=?, updated=? WHERE id=?", (new, int(time.time()), sid))
        db.commit()
        print("written; backup", bak)


if __name__ == "__main__":
    main()
