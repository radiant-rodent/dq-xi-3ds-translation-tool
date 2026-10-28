"""lumasim.py - boot the LayeredFS zip in Azahar with Luma3DS's own code patching simulated.

    python emutest/lumasim.py [--zip docs/patch/DQXI_EN_layeredfs.zip] [--save user_citra|none] [--secs 90]

A real 3DS runs the zip's code.bin after Luma's loader wrote its RomFS redirection payload to
the end of .text and its mod path to the end of .rodata (dq11tool/luma.py); Azahar's LayeredFS
does not touch the code, so a collision crashes only on hardware. This installs the zip as the
test mod with luma.simulate() applied (payload area = undefined instructions: any use of it
crashes at once), boots the Japanese ROM, presses A every 1.5 s from the title screen (with a
save: Continue, slot 1, then the field and its talk) and fails on a guest crash. The test mod
that was installed before is put back afterwards. Static version: tests/luma_test.py.
"""
import argparse
import os
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import az                                   # noqa: E402
from dq11tool import luma                   # noqa: E402
from dq11tool.ctr.formats import open_image  # noqa: E402

MODS = os.path.join(az.USER, "load", "mods", "0004000000199200")
OUT = os.path.join(az.ROOT, "runs", "lumasim")


def install(zpath: str, exheader: bytes):
    prefix = "luma/titles/0004000000199200/"
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            data = z.read(name)
            if name == prefix + "code.bin":
                p = os.path.join(MODS, "exefs", "code.bin")
                data = luma.simulate(data, exheader)
            elif name.startswith(prefix + "romfs/"):
                p = os.path.join(MODS, "romfs", *name[len(prefix) + 6:].split("/"))
            else:
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fp:
                fp.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.join(az.REPO, "docs", "patch", "DQXI_EN_layeredfs.zip"))
    ap.add_argument("--save", default="user_citra", help="'none': no save (New Game, opening movie)")
    ap.add_argument("--secs", type=float, default=90, help="game seconds of A presses")
    a = ap.parse_args()
    f, _n, ncch = open_image(az.JP_ROM)
    exheader = ncch.exheader()
    f.close()

    backup = MODS + ".lumasim-backup"
    if os.path.isdir(backup):
        sys.exit(f"{backup} exists (an earlier run did not finish): put it back as {MODS} first")
    if os.path.isdir(MODS):
        os.rename(MODS, backup)
    os.makedirs(OUT, exist_ok=True)
    try:
        install(a.zip, exheader)
        m = az.Movie()
        t = 6.0
        while t < a.secs:
            m.press("A", at=t)
            t += 1.5
        save = None if a.save == "none" else a.save
        shots, last, crash = [], 0.0, None
        with az.Azahar(az.JP_ROM, movie=m, save=save, workdir=OUT) as e:
            while True:
                gt = e.game_time()
                crash = e.crashed()
                if crash or gt > a.secs + 20:
                    break
                if gt - last >= 10:
                    last = gt
                    p = os.path.join(OUT, f"t{int(gt):04d}.png")
                    if e.shot(p):
                        shots.append(p)
                time.sleep(0.05)
            crash = crash and (e.first_crash() or crash)
        if shots:
            az.sheet(shots[-12:], os.path.join(OUT, "sheet.png"))
    finally:
        az.kill_test_emulators()
        if os.path.isdir(MODS):
            shutil.rmtree(MODS)
        if os.path.isdir(backup):
            os.rename(backup, MODS)
    if crash:
        sys.exit(f"CRASH at {gt:.1f} s game time: {crash}  (log: {az.LOG})")
    print(f"ok: no crash in {gt:.0f} s game time with Luma's LayeredFS writes simulated ({OUT}/sheet.png)")


if __name__ == "__main__":
    main()
