"""heaps.py - free memory of the game's heaps (test builds with the heap_track probe).

The probe records every heap the general allocator (0x55E928) is called for at 0x839D00
(16 x [heap object, largest request]). Each heap keeps a circular list of free blocks with
its sentinel at heap+0x14: first free node = [heap+0x20], node+0xC = next, node+0x10 =
size (bit 31 = in use). Prints, per heap: free bytes, largest free block, block count and
the first header words (base/size fields)."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEAPS = 0x839D00


def walk(read, heap, limit=200000):
    sentinel = heap + 0x14
    node = struct.unpack("<I", read(heap + 0x20, 4))[0]
    free = largest = n = 0
    while node != sentinel and n < limit:
        if node == 0:
            return free, largest, n, "BROKEN (null next)"
        nxt, size = struct.unpack("<II", read(node + 0xC, 8))
        size &= 0x7FFFFFFF
        free += size
        largest = max(largest, size)
        n += 1
        node = nxt
    return free, largest, n, "" if n < limit else "loop?"


def report(read):
    tab = struct.unpack("<32I", read(HEAPS, 128))
    out = []
    for i in range(16):
        heap, biggest = tab[2 * i], tab[2 * i + 1]
        if not heap:
            break
        hdr = struct.unpack("<12I", read(heap, 48))
        free, largest, n, note = walk(read, heap)
        out.append((heap, biggest, free, largest, n, note, hdr))
    return out


if __name__ == "__main__":
    from azctl import Session
    s = Session()
    for heap, biggest, free, largest, n, note, hdr in report(s.read):
        print(f"heap {heap:08x}  max request {biggest:>9}  free {free:>10}  largest {largest:>10}  "
              f"blocks {n:>5} {note}\n   hdr " + " ".join(f"{w:08x}" for w in hdr))
