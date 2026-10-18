"""bxtree.py FILE.bxon [ROOT_INDEX] [DEPTH] - print the object tree of a bxon (arrays found as
(count, relptr) pairs pointing at '&&&&' blocks, strings decoded)."""
import struct
import sys

sys.path.insert(0, __file__.rsplit("emutest", 1)[0])
from dq11tool.bxon import _is_string_block  # noqa: E402
from dq11tool.textcodec import decode_utf16  # noqa: E402

d = open(sys.argv[1], "rb").read()
n = len(d)
blocks = {}
i = d.find(b"&&&&")
starts = []
while i >= 0:
    if i % 4 == 0:
        starts.append(i + 4)
    i = d.find(b"&&&&", i + 4)
for a, b in zip(starts, starts[1:] + [n + 4]):
    blocks[a] = b - 4 - a


def u32(p):
    return struct.unpack_from("<I", d, p)[0] if 0 <= p <= n - 4 else None


def is_str(t):
    return t in blocks and _is_string_block(d[t:t + blocks[t]])


def show(base, count, esize, depth, ind):
    for e in range(count):
        p0 = base + e * esize
        print(f"{ind}[{e}] @{p0:#x} size {esize}")
        k = 0
        while k < esize:
            p = p0 + k
            c, v = u32(p), u32(p + 4) if k + 8 <= esize else None
            t2 = (p + 4 + v) & 0xFFFFFFFF if v else None
            if c and v and c < 0x10000 and t2 in blocks and not is_str(t2) and blocks[t2] % c == 0:
                es = blocks[t2] // c
                print(f"{ind}  +{k:#x}: array x{c} -> {t2:#x} (elem {es})")
                if depth > 0:
                    show(t2, min(c, 3), es, depth - 1, ind + "    ")
                k += 8
                continue
            v = u32(p)
            t = (p + v) & 0xFFFFFFFF if v else None
            if t is not None and is_str(t):
                s = decode_utf16(d[t:t + blocks[t]].rstrip(b"@")).rstrip("\0")
                print(f"{ind}  +{k:#x}: str @{t:#x} {s[:60]!r}")
            elif t is not None and t in blocks:
                print(f"{ind}  +{k:#x}: ptr -> {t:#x} (block {blocks[t]})")
                if depth > 0:
                    show(t, 1, blocks[t], depth - 1, ind + "    ")
            else:
                print(f"{ind}  +{k:#x}: {v:#010x}")
            k += 4


rc, rp = u32(0x20), u32(0x24)
root = (0x24 + rp) & 0xFFFFFFFF
ri = int(sys.argv[2]) if len(sys.argv) > 2 else 0
depth = int(sys.argv[3]) if len(sys.argv) > 3 else 4
es = blocks[root] // rc
print(f"root x{rc} -> {root:#x} elem {es}")
show(root + ri * es, 1, es, depth, "")
