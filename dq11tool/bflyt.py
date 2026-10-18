"""BFLYT (FLYT v7.2.1, little endian) text pane editing.

txt1 section (offsets relative to section start):
  0x00 magic, 0x04 size, 0x08 pane flags, 0x0C name[0x18], 0x24 user data[8],
  0x2C translate/rotate/scale/size floats ... (pane header ends at 0x54)
  0x54 u16 textBufBytes   (runtime buffer the game allocates for this box)
  0x56 u16 textStrBytes   (initial string length in bytes incl. NUL)
  0x58 u16 material, u16 font, u8 position, u8 alignment, u8 flags, u8 pad
  0x60 f32 italic ratio
  0x64 u32 textOffset     0x68 u32 colors[2]   0x70 f32 font size x,y
  0x78 f32 char space, line space            0x80 u32 textIdOffset
  0x84.. shadow params ...                    0xA0 u32 per-char transform offset (0 = none)
  then text (textBufBytes, 4-aligned), then textId string, then optional extra data.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

PANES = (b"pan1", b"pic1", b"txt1", b"wnd1", b"bnd1", b"prt1")

# Pane texts made of these only are numbers, prices, counters or single symbols ("99999G",
# "+800", "99.5%", "×９９", "："): the game fills them with numbers, which are no longer in
# English, so their runtime buffers are not grown (every grown buffer costs layout memory;
# growing all ~180 panes of menu_dougu_details_2d x2 overflowed the game's heap).
_NUMERIC = set("0123456789０１２３４５６７８９+-－＋×x%％.．,，:：/／()（）[]［］ 　G Ｇヰ\n")


# pane user data (8 bytes, empty in every original layout) of text panes the game narrows at
# runtime when a string is wider (one-line label, autofit.MARKER) or, wrapped, taller (a
# multi-line box, autofit.MARKER_MULTI) than the pane
FIT_MARKER = b"fit"
FIT_MARKER_MULTI = b"fim"
WINDOW_MARGIN = 7.0     # px between a window pane's edge and its text (frame + padding)
NEIGHBOUR_GAP = 5.0     # px between a label and the next text on its row
JP_TOLERANCE = 4.0      # px the Japanese placeholder text may pass a limit and it still counts
LATIN_WIDTH = 0.66      # average Latin glyph advance / font size x (3D12: "No Armour" 77 px at 13)
LABEL_MAX = 60.0        # px: a left text pane this narrow before a value pane is its label
VALUE_ROOM = 1.3        # a right-aligned value may grow to this times its placeholder ("80%" -> "100%")


def one_line(sec) -> bool:
    """A txt1 section whose pane is less than two lines of its font high."""
    h = struct.unpack_from("<f", sec, 0x50)[0]
    fy = struct.unpack_from("<f", sec, 0x74)[0]
    return 0 < h < 2 * fy


def box_holds(sec) -> bool:
    """A multi-line txt1 section whose own (Japanese placeholder) lines fit its height: a box
    the text is meant to stay in (not a pane its text runs out of on purpose)."""
    h = struct.unpack_from("<f", sec, 0x50)[0]
    fy = struct.unpack_from("<f", sec, 0x74)[0]
    ls = struct.unpack_from("<f", sec, 0x7C)[0]
    n = Bflyt._pane_text(sec).rstrip("\n").count("\n") + 1
    return h > 0 and fy > 0 and n * fy + (n - 1) * ls <= h + JP_TOLERANCE


def numeric_text(text: str) -> bool:
    t = text.strip()
    return not t or all(ch in _NUMERIC for ch in t) or len(t) <= 1


@dataclass
class TextPane:
    index: int          # section index
    sec_offset: int     # section start in file
    name: str
    text: str
    buf_bytes: int
    str_bytes: int
    text_id: str
    width: float
    height: float
    font_index: int
    font_size: tuple
    scale: tuple = (1.0, 1.0)   # own scale times the scale of all parent panes


class Bflyt:
    def __init__(self, data: bytes):
        if data[:4] != b"FLYT":
            raise ValueError("not FLYT")
        self.data = data
        self.header_size = struct.unpack_from("<H", data, 6)[0]
        self.version = struct.unpack_from("<I", data, 8)[0]
        self.nsec = struct.unpack_from("<H", data, 0x10)[0]
        self.sections: list[tuple[bytes, int, int]] = []  # (magic, offset, size)
        p = self.header_size
        for _ in range(self.nsec):
            m, sz = struct.unpack_from("<4sI", data, p)
            self.sections.append((m, p, sz))
            p += sz
        self.fonts = self._fonts()
        self.grow_only = None        # tests: grow only the text panes with these names
        self.min_buf: dict[str, int] = {}   # pane name -> minimum text buffer (bytes)
        # pane name -> (font size y, line space): e.g. four message lines in a 3-line pane
        self.text_style: dict[str, tuple[float, float]] = {}
        self.move: dict[str, tuple[float, float]] = {}   # pane name -> (dy, dheight) in pixels
        self.move_x: dict[str, float] = {}               # pane name -> dx in pixels (translate x)
        self.set_width: dict[str, float] = {}            # pane name -> width (any pane kind)
        # pane name -> {section offset: value}: raw edits (maplabel.py: position, origin, alignment)
        self.set_float: dict[str, dict[int, float]] = {}
        self.set_byte: dict[str, dict[int, int]] = {}
        # section index -> factor for the font size x (glyph width) of a text pane: fits a
        # longer English label into its pane without moving or widening anything
        self.squeeze: dict[int, float] = {}
        self.squeeze_names: dict[str, float] = {}       # the same by pane name
        self.runtime_overflow: dict[str, float] = {}    # px still too wide at the minimum squeeze (report only)
        self.width_to: dict[str, float] = {}            # pane name -> new width (px)
        # runtime fit (autofit.py): None = off, else pane names to leave out; every other
        # one-line text pane gets the user data FIT_MARKER, a box FIT_MARKER_MULTI
        self.mark_fit: set[str] | None = None
        self.mark_multi = True

    def _fonts(self) -> list[str]:
        for m, off, sz in self.sections:
            if m == b"fnl1":
                n = struct.unpack_from("<H", self.data, off + 8)[0]
                names = []
                for i in range(n):
                    o = struct.unpack_from("<I", self.data, off + 12 + i * 4)[0]
                    s = off + 12 + o
                    names.append(self.data[s:self.data.index(b"\0", s)].decode("ascii", "replace"))
                return names
        return []

    def text_panes(self) -> list[TextPane]:
        out = []
        d = self.data
        stack, last = [], (1.0, 1.0)
        for i, (m, off, sz) in enumerate(self.sections):
            if m in (b"pan1", b"pic1", b"txt1", b"wnd1", b"bnd1", b"prt1"):
                sx, sy = struct.unpack_from("<ff", d, off + 0x44)
                px, py = stack[-1] if stack else (1.0, 1.0)
                last = (px * sx, py * sy)
            elif m == b"pas1":
                stack.append(last)
            elif m == b"pae1" and stack:
                stack.pop()
            if m != b"txt1":
                continue
            name = d[off + 0x0C:off + 0x24].split(b"\0", 1)[0].decode("ascii", "replace")
            w, h = struct.unpack_from("<ff", d, off + 0x4C)
            buf, strb, mat, font = struct.unpack_from("<HHHH", d, off + 0x54)
            toff = struct.unpack_from("<I", d, off + 0x64)[0]
            fsx, fsy = struct.unpack_from("<ff", d, off + 0x70)
            tid_off = struct.unpack_from("<I", d, off + 0x80)[0]
            raw = d[off + toff:off + toff + max(strb - 2, 0)]
            text = raw.decode("utf-16-le", errors="surrogatepass").split("\0", 1)[0]
            tid = ""
            if tid_off:
                e = d.index(b"\0", off + tid_off)
                tid = d[off + tid_off:e].decode("ascii", "replace")
            out.append(TextPane(i, off, name, text, buf, strb, tid, w, h, font, (fsx, fsy), last))
        return out

    def rebuild(self, new_texts: dict[int, str] | None = None, buf_factor: float = 1.0,
                buf_min: int = 0, widen: dict[int, float] | None = None) -> bytes:
        """new_texts: section index -> new (raw, already encoded control codes) string.
        buf_factor/buf_min enlarge every text box's runtime buffer.
        widen: section index -> factor for a pane's width (text, window or any other pane)."""
        new_texts = new_texts or {}
        widen = widen or {}
        d = self.data
        clamp = {}
        if self.mark_fit is not None and not widen:
            probe = self
            if self.width_to:            # clamp against the widths the export sets
                tmp = bytearray(d)
                for m, off, sz in self.sections:
                    nm = d[off + 0x0C:off + 0x24].split(b"\0", 1)[0].decode("ascii", "replace")
                    if m == b"txt1" and nm in self.width_to:
                        w = struct.unpack_from("<f", d, off + 0x4C)[0]
                        struct.pack_into("<f", tmp, off + 0x4C, max(w, self.width_to[nm]))
                probe = Bflyt(bytes(tmp))
            clamp = probe.window_clamps(new_texts=new_texts)
        out = bytearray(d[:self.header_size])
        for i, (m, off, sz) in enumerate(self.sections):
            sec = bytearray(d[off:off + sz])
            if m == b"txt1":
                grow = not numeric_text(self._pane_text(sec))
                pname = sec[0x0C:0x24].split(b"\0", 1)[0].decode("ascii", "replace")
                if grow and self.grow_only is not None:
                    grow = pname in self.grow_only
                bmin = buf_min if grow else 0
                if pname in self.min_buf:
                    bmin = max(bmin, self.min_buf[pname])
                sec = self._rebuild_txt1(sec, new_texts.get(i), buf_factor if grow else 1.0, bmin)
                sq = min(self.squeeze.get(i, 1.0), self.squeeze_names.get(pname, 1.0))
                if sq < 1.0:
                    fx = struct.unpack_from("<f", sec, 0x70)[0]
                    struct.pack_into("<f", sec, 0x70, fx * sq)
                if pname in self.width_to:
                    struct.pack_into("<f", sec, 0x4C, max(self.width_to[pname],
                                                         struct.unpack_from("<f", sec, 0x4C)[0]))
                if pname in self.text_style:
                    fy, ls = self.text_style[pname]
                    struct.pack_into("<f", sec, 0x74, fy)
                    struct.pack_into("<f", sec, 0x7C, ls)
                # number panes ("27", "308"): the runtime fit never narrows text without letters,
                # so a clamped number wrapped instead (2D battle "MP 30 / 8", user 2026-10-17)
                if (self.mark_fit is not None and pname not in self.mark_fit and not any(sec[0x24:0x2C])
                        and not numeric_text(self._pane_text(d[off:off + sz]))):
                    if one_line(sec):
                        sec[0x24:0x2C] = FIT_MARKER.ljust(8, b"\0")
                        if i in clamp and widen.get(i, 1) <= 1 and pname not in self.width_to:
                            cw, dx = clamp[i]
                            struct.pack_into("<f", sec, 0x4C, cw)
                            if dx:
                                struct.pack_into("<f", sec, 0x2C, struct.unpack_from("<f", sec, 0x2C)[0] + dx)
                    elif self.mark_multi and box_holds(d[off:off + sz]):
                        sec[0x24:0x2C] = FIT_MARKER_MULTI.ljust(8, b"\0")
            if m in PANES and (self.move or self.move_x or self.set_width or self.set_float or self.set_byte):
                mname = sec[0x0C:0x24].split(b"\0", 1)[0].decode("ascii", "replace")
                if mname in self.move:
                    dy, dh = self.move[mname]
                    struct.pack_into("<f", sec, 0x30, struct.unpack_from("<f", sec, 0x30)[0] + dy)
                    struct.pack_into("<f", sec, 0x50, struct.unpack_from("<f", sec, 0x50)[0] + dh)
                if mname in self.move_x:
                    struct.pack_into("<f", sec, 0x2C, struct.unpack_from("<f", sec, 0x2C)[0] + self.move_x[mname])
                if mname in self.set_width:
                    struct.pack_into("<f", sec, 0x4C, self.set_width[mname])
                for o, v in self.set_float.get(mname, {}).items():
                    struct.pack_into("<f", sec, o, v)
                for o, v in self.set_byte.get(mname, {}).items():
                    sec[o] = v
            # a text pane anchored right or centre with left-aligned text would move its text
            # start left (into the column before it: 2D "Abilities" over "Attack"); not widened
            moves_text = m == b"txt1" and sec[9] & 3 != 1 and sec[0x5C] & 3 == 1
            if m in PANES and widen.get(i, 1) > 1 and not moves_text:
                w = struct.unpack_from("<f", sec, 0x4C)[0]
                struct.pack_into("<f", sec, 0x4C, w * widen[i])
            out += sec
        struct.pack_into("<I", out, 0x0C, len(out))
        return bytes(out)

    def window_clamps(self, margin: float = WINDOW_MARGIN,
                      new_texts: dict[int, str] | None = None) -> dict[int, tuple[float, float]]:
        """section index -> (new width, x shift) for text panes reaching further than their text
        may go, so the runtime fit (autofit.py, which narrows a string to its pane width) keeps
        it clear:
          - left-aligned text: the inner right edge of the window frame it sits in, and the
            next text on its row: a left-aligned pane's left edge, a right-aligned value's
            placeholder width ("Beguilement Protection" | "20%", 2D "Attack" | "Abilities")
          - right-aligned text (a value): the end of the label text on its left
            ("Buy" | "Can't buy": the label pane, or its translation in new_texts if wider)
        The edge the text starts from stays where it is (the pane moves when its anchor is on
        the other side; not done for panes with children). An edge the layout's Japanese
        placeholder text already passes is not a limit (that neighbour is shown at other
        times, that window is sized at runtime). Positions are translation sums (no rotation;
        scales multiply)."""
        d = self.data
        stack, cur, owners, last = [], (0.0, 0.0, 1.0, 1.0), [], None
        parent: dict[int, int | None] = {}
        has_children: set[int] = set()
        geo: dict[int, tuple[float, float, float]] = {}   # index -> (origin fraction, scale, parent scale)
        # (section index, magic, left, right, top, bottom, origin x, text x, Japanese width)
        boxes = []
        for i, (m, off, sz) in enumerate(self.sections):
            if m == b"pas1":
                stack.append(cur)
                owners.append(last)
                if last is not None:
                    has_children.add(last)
                continue
            if m == b"pae1":
                if stack:
                    stack.pop()
                    owners.pop()
                continue
            if m not in PANES:
                continue
            parent[i] = owners[-1] if owners else None
            last = i
            px, py, psx, psy = stack[-1] if stack else (0.0, 0.0, 1.0, 1.0)
            tx, ty = struct.unpack_from("<ff", d, off + 0x2C)
            sx, sy = struct.unpack_from("<ff", d, off + 0x44)
            w, h = struct.unpack_from("<ff", d, off + 0x4C)
            x, y = px + tx * psx, py + ty * psy
            cur = (x, y, psx * sx, psy * sy)
            ox, oy = d[off + 9] & 3, (d[off + 9] >> 2) & 3
            fx = {0: 0.5, 1: 0.0, 2: 1.0}.get(ox, 0.5)
            fy = {0: 0.5, 1: 0.0, 2: 1.0}.get(oy, 0.5)
            ww, hh = w * cur[2], h * cur[3]
            left, top = x - ww * fx, y + hh * fy
            geo[i] = (fx, cur[2] or 1.0, psx or 1.0)
            jw = 0.0
            if m == b"txt1":   # how far the layout's own (Japanese placeholder) text reaches
                fsx = struct.unpack_from("<f", d, off + 0x70)[0] * cur[2]
                lines = self._pane_text(d[off:off + sz]).split("\n")
                jw = max(sum(fsx if ord(c) >= 0x2000 else fsx * 0.55 for c in ln) for ln in lines)
            boxes.append((i, m, left, left + ww, top, top - hh, ox,
                          d[off + 0x5C] & 3 if m == b"txt1" else None, jw))
        wins = [b for b in boxes if b[1] == b"wnd1" and b[3] - b[2] > 2 * margin]
        texts = [b for b in boxes if b[1] == b"txt1"]

        def same_row(a, b):
            ca, cb = (a[4] + a[5]) / 2, (b[4] + b[5]) / 2
            return abs(ca - cb) < min(a[4] - a[5], b[4] - b[5]) / 2

        def label_width(n):
            """Estimated width of a label's final text (translation if any, else Japanese)."""
            t = (new_texts or {}).get(n[0])
            if t is None:
                return n[8]
            fsx = struct.unpack_from("<f", d, self.sections[n[0]][1] + 0x70)[0]
            vis = [c for c in t.split("\n")[0] if ord(c) >= 0x20 and not 0xE000 <= ord(c) < 0xF900]
            return sum(fsx if ord(c) >= 0x2000 else fsx * LATIN_WIDTH for c in vis)

        def inside_of(n, b):
            p = parent.get(n[0])
            while p is not None:
                if p == b[0]:
                    return True
                p = parent.get(p)
            return False

        out = {}
        for b in texts:
            i, m, left, right, top, bottom, ox, tx, jw = b
            new_left, new_right = left, right
            # an edge the Japanese text already passes is not a real limit (the neighbour is
            # shown at other times, the window is sized at runtime) - unless the neighbour is
            # a child of the pane (shown with it: the "80%" of an effect line)
            if tx == 1:                      # text from the left edge: window edge, next text
                reach = left + jw - JP_TOLERANCE
                cy = (top + bottom) / 2
                up, p = set(), parent.get(i)       # the pane's group and the groups around it
                while p is not None:
                    up.add(p)
                    p = parent.get(p)
                inside = [w for w in wins if w[2] <= left < w[3] and w[5] <= cy <= w[4]
                          and parent.get(w[0]) in up]
                if inside:
                    edge = min(inside, key=lambda w: w[3] - w[2])[3] - margin
                    if edge >= reach:
                        new_right = min(new_right, edge)
                for n in texts:
                    # only panes of its group (siblings) or its children: the other groups of a
                    # layout are often other pages drawn over the same area
                    if n is b or not same_row(b, n) or not (parent.get(n[0]) == parent.get(i)
                                                            or inside_of(n, b)):
                        continue
                    if n[7] == 1:                # left-aligned: its text starts at its left edge
                        start = n[2]
                    elif n[7] == 2 and n[8]:     # right-aligned value: about its placeholder's width
                        start = n[3] - n[8] * VALUE_ROOM
                    else:
                        continue
                    limit = start - NEIGHBOUR_GAP
                    if left + 8 < start < new_right and (limit >= reach or inside_of(n, b)):
                        new_right = limit
            elif tx == 2:                    # text from the right edge: the label before it
                for n in texts:
                    if n is not b and n[7] == 1 and same_row(b, n) \
                            and parent.get(n[0]) == parent.get(i) \
                            and n[2] < left + 8 and left < n[3] < right - 8 and n[3] - n[2] <= LABEL_MAX:
                        # the label pane (labels are short panes; its text may be set at runtime)
                        end = n[2] + max(label_width(n), n[3] - n[2]) + NEIGHBOUR_GAP
                        if left < end:
                            new_left = max(new_left, end)
            if (new_left, new_right) == (left, right):
                continue
            if new_right - new_left < (right - left) * 0.4:
                continue
            fx, sc, psc = geo[i]
            # the anchor (origin point) moves with the shortened side
            dx = (new_left + (new_right - new_left) * fx) - (left + (right - left) * fx)
            if abs(dx) > 0.01 and i in has_children:
                continue                 # its children would move with it
            out[i] = (round((new_right - new_left) / sc, 1), round(dx / psc, 2))
        return out

    def _scale_of(self, index: int) -> float:
        for p in self.text_panes():
            if p.index == index:
                return p.scale[0] or 1.0
        return 1.0

    def widen_window(self, factor: float) -> dict[int, float]:
        """Widen factors (for rebuild) that make a window layout's text panes `factor` times
        wider and grow its window frames by the same number of pixels. Only for left-anchored
        windows: the frame grows to the right."""
        txt, wnd = [], []
        for i, (m, off, sz) in enumerate(self.sections):
            if m in (b"txt1", b"wnd1"):
                (txt if m == b"txt1" else wnd).append((i, struct.unpack_from("<f", self.data, off + 0x4C)[0]))
        if factor <= 1 or not txt:
            return {}
        grow = max(w for _, w in txt) * (factor - 1)
        out = {i: factor for i, _ in txt}
        out.update({i: (w + grow) / w for i, w in wnd if w > 0})
        return out

    @staticmethod
    def _pane_text(sec) -> str:
        strb = struct.unpack_from("<H", sec, 0x56)[0]
        toff = struct.unpack_from("<I", sec, 0x64)[0]
        raw = bytes(sec[toff:toff + max(strb - 2, 0)])
        return raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]

    @staticmethod
    def _rebuild_txt1(sec: bytearray, new_text: str | None, factor: float, bmin: int) -> bytearray:
        buf, strb = struct.unpack_from("<HH", sec, 0x54)
        toff = struct.unpack_from("<I", sec, 0x64)[0]
        old_region = (buf + 3) & ~3
        if new_text is not None:
            raw = new_text.encode("utf-16-le", errors="surrogatepass") + b"\0\0"
        else:
            raw = bytes(sec[toff:toff + strb])
        new_strb = len(raw)
        new_buf = max(buf, new_strb)
        if factor > 1 or bmin:
            new_buf = max(new_buf, int(buf * factor), bmin)
        new_buf = min((new_buf + 1) & ~1, 0xFFFE)
        if new_strb > new_buf:
            raw = raw[:new_buf - 2] + b"\0\0"
            new_strb = new_buf
        # The game uses the text region inside the loaded layout as the runtime buffer, so
        # the file must reserve all textBufBytes (every original layout does). Storing less
        # lets the game write past the section into neighbouring heap data.
        new_region = (new_buf + 3) & ~3
        region = raw + b"\0" * (new_region - new_strb)
        delta = new_region - old_region
        head = sec[:toff]
        tail = sec[toff + old_region:]
        # shift offsets that point past the text region
        for fo in (0x80, 0xA0):
            if fo + 4 <= len(head):
                v = struct.unpack_from("<I", head, fo)[0]
                if v and v >= toff + old_region:
                    struct.pack_into("<I", head, fo, v + delta)
        struct.pack_into("<HH", head, 0x54, new_buf, new_strb)
        out = head + region + tail
        struct.pack_into("<I", out, 4, len(out))
        return out
