"""pools.py - usage of the game's small-block allocator (object at 0x7F50F0, alloc 0x6F1C9C).

Five size classes, each [free list head, pool base, block size, block count] at
+4 + 16*i; free blocks are linked through their first word. Sizes above the largest class,
and requests a class cannot serve, go to the general heap at +0x54 (0x7F5144).
Prints free/total blocks per class (pool memory is read in bulk and walked offline)."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALLOC = 0x7F50F0


def usage(read):
    out = []
    for i in range(5):
        head, base, bsize, count = struct.unpack("<4I", read(ALLOC + 4 + 16 * i, 16))
        size = bsize * count
        mem = bytearray()
        for a in range(base, base + size, 0x8000):
            mem += read(a, min(0x8000, base + size - a))
        free, node, seen, prev = 0, head, set(), None
        while node and free <= count:
            if not (base <= node < base + size) or node in seen:
                out.append((bsize, count, free, f"BAD node {node:08x}", prev, base, bytes(mem)))
                break
            prev = node
            seen.add(node)
            free += 1
            node = struct.unpack_from("<I", mem, node - base)[0]
        else:
            out.append((bsize, count, free, "", None, base, None))
    return out


if __name__ == "__main__":
    from azctl import Session
    s = Session()
    for bsize, count, free, note, *_ in usage(s.read):
        print(f"class {bsize:#5x}: {count - free:5} used / {count:5}  ({free} free) {note}")
