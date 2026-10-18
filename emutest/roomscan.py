"""roomscan.py FIRST LAST [--area 35] [--pos X,Y,Z] - find a room id by loading edited saves.

A slot file's location block (0x6580): u32 kind, 2 pad words, position x,y,z (0x658C), facing
x,y,z, u32 area (0x65A8), u32 room (0x65AC). For every room id in FIRST..LAST this writes
slot 1 of the user_citra save with that room and position, boots it and keeps one screenshot
(_emutest/runs/roomscan/room_NNN.png) plus a contact sheet (sheet.png).
"""
import os
import shutil
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az        # noqa: E402
import azctl     # noqa: E402

OUT = os.path.join(az.ROOT, "runs", "roomscan")


def fix_crc(d: bytearray) -> bytearray:
    struct.pack_into("<II", d, len(d) - 8, 0x5E, zlib.crc32(bytes(d[:-8])) & 0xFFFFFFFF)
    return d


def make_save(name: str, area: int, room: int, pos, facing=(0.0, 0.0, -1.0), kind: int = 2):
    src = os.path.join(az.SAVES, "user_citra")
    dst = os.path.join(az.SAVES, name)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    p = os.path.join(dst, "00000001", "Data0")
    d = bytearray(open(p, "rb").read())
    struct.pack_into("<I", d, 0x6580, kind)
    struct.pack_into("<3f", d, 0x658C, *pos)
    struct.pack_into("<3f", d, 0x6598, *facing)
    struct.pack_into("<II", d, 0x65A8, area, room)
    open(p, "wb").write(fix_crc(d))


def main(a):
    first, last = int(a[0]), int(a[1])
    area = int(a[a.index("--area") + 1]) if "--area" in a else 35
    pos = tuple(float(x) for x in (a[a.index("--pos") + 1] if "--pos" in a else "3.73,0.5,3.03").split(","))
    os.makedirs(OUT, exist_ok=True)
    shots = []
    for room in range(first, last + 1):
        make_save("roomscan", area, room, pos)
        try:
            azctl.main(["start", "--save", "roomscan", "--boot", "1"])
            s = azctl.Session()
            s.wait_game(s.game_time() + 2)
            p = os.path.join(OUT, f"room_{room:03d}.png")
            s.shot(p)
            shots.append(p)
            print("room", room, "crash" if s.crashed() else "ok", flush=True)
        except Exception as e:
            print("room", room, "error", e, flush=True)
        finally:
            azctl.main(["stop"])
    if shots:
        az.sheet(shots, os.path.join(OUT, "sheet.png"), cols=6, scale=0.5)


if __name__ == "__main__":
    main(sys.argv[1:])
