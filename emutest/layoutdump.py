"""layoutdump.py PACK MEMBER [PACK MEMBER ...] - pane tree of original layouts (JP ROM):
origin flags, position, size; text panes with font size and line spacing."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dq11tool.ctr.formats import open_image  # noqa: E402
from dq11tool.darc import Sarc  # noqa: E402
from dq11tool.pack import Pack  # noqa: E402
import az  # noqa: E402


def members(pack: str):
    f, _, ncch = open_image(az.JP_ROM)
    try:
        r = ncch.romfs()
        d = r.read_file(next(x for x in r.files if x.path == pack))
    finally:
        f.close()
    out = {}
    for e in Pack.parse(d).entries:
        if e.data[:4] == b"SARC":
            s = Sarc.parse(e.data)
            for n in s.nodes:
                out[n.name] = s.raw[s.data_offset + n.start:s.data_offset + n.end]
    return out


def dump(md: bytes):
    p, depth = struct.unpack_from("<H", md, 6)[0], 0
    for _ in range(struct.unpack_from("<H", md, 0x10)[0]):
        m, sz = struct.unpack_from("<4sI", md, p)
        if m == b"pas1":
            depth += 1
        elif m == b"pae1":
            depth -= 1
        elif m in (b"pan1", b"txt1", b"wnd1", b"pic1", b"prt1", b"bnd1"):
            name = md[p + 0xC:p + 0x24].split(b"\0")[0].decode()
            tx, ty = struct.unpack_from("<ff", md, p + 0x2C)
            w, h = struct.unpack_from("<ff", md, p + 0x4C)
            extra = ""
            if m == b"txt1":
                fx, fy, cs, ls = struct.unpack_from("<4f", md, p + 0x70)
                extra = f"  font {fx:g}x{fy:g} line+{ls:g}"
            print(f"{'  ' * depth}{m.decode()} {name} org {md[p + 9]:02x} vis {md[p + 8] & 1}"
                  f" pos {tx:g},{ty:g} size {w:g}x{h:g}{extra}")
        p += sz


if __name__ == "__main__":
    a = sys.argv[1:]
    for pack, member in zip(a[::2], a[1::2]):
        print(f"== {pack} {member}")
        dump(members(pack)[member])
