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
        for i, (m, off, sz) in enumerate(self.sections):
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
            out.append(TextPane(i, off, name, text, buf, strb, tid, w, h, font, (fsx, fsy)))
        return out

    def rebuild(self, new_texts: dict[int, str] | None = None, buf_factor: float = 1.0,
                buf_min: int = 0) -> bytes:
        """new_texts: section index -> new (raw, already encoded control codes) string.
        buf_factor/buf_min enlarge every text box's runtime buffer."""
        new_texts = new_texts or {}
        d = self.data
        out = bytearray(d[:self.header_size])
        for i, (m, off, sz) in enumerate(self.sections):
            sec = bytearray(d[off:off + sz])
            if m == b"txt1":
                sec = self._rebuild_txt1(sec, new_texts.get(i), buf_factor, buf_min)
            out += sec
        struct.pack_into("<I", out, 0x0C, len(out))
        return bytes(out)

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
