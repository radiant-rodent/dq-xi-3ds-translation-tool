"""luma_test.py - the LayeredFS download must survive Luma3DS's own code patching on a real 3DS.

    python tests/luma_test.py [JAPANESE.3ds] [ZIP]

Run before every push that changes docs/patch (CLAUDE.md). Luma writes its RomFS redirection
payload into the end of .text and its mod path into the end of .rodata (dq11tool/luma.py);
Azahar does not, so emulator tests never see a collision (2026-10-28: a real 3DS crashed right
after the title screen). Checks:

1. dq11tool/luma.py models Luma's placement: the payload lands at the end of .text, Luma finds
   the fs functions it hooks in the original game.
2. No release code cave (autofit, namefix, grammar, word wrap) lies in Luma's areas.
3. The published zip (default docs/patch/DQXI_EN_layeredfs.zip): its code.bin changes no byte
   Luma overwrites or hooks, and nothing outside luma/titles/<title id>/{romfs/,code.bin}.

The emulator version of the same check: emutest/lumasim.py (boots the zip with Luma's writes
simulated).
"""
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from dq11tool import autofit, blz, grammar_code, luma, namefix, wordwrap   # noqa: E402
from dq11tool.ctr.formats import open_image                                 # noqa: E402

TITLE = "0004000000199200"


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "DQXI.3ds")
    zpath = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "docs", "patch", "DQXI_EN_layeredfs.zip")
    f, _ncsd, ncch = open_image(rom)
    try:
        exheader = ncch.exheader()
        original = ncch.exefs().read(".code")
    finally:
        f.close()
    if exheader[0x0D] & 1:
        original = blz.decompress(original)

    # 1. the model
    regions = luma.regions(exheader)
    (text_addr, _p, text_size) = luma.segments(exheader)[0]
    assert regions[0][0] == text_addr + text_size == 0x72403C, regions
    sites = luma.hook_sites(original, exheader)
    assert {"fsOpenFileDirectly", "fsTryOpenFile", "fsMountArchive", "fsRegisterArchive"} <= set(sites), sites

    # 2. release caves outside Luma's areas
    caves = {"grammar": (grammar_code.CAVE, len(grammar_code.CODE)),
             "autofit": (autofit.CAVE, len(autofit.CODE)),
             "autofit bitmap": (autofit.BITMAP_AT, 32),
             "autofit scale": (autofit.SCALE_AT, len(autofit.SCALE_CODE)),
             "autofit remap": (autofit.REMAP_AT, len(autofit.REMAP_CODE)),
             "namefix": (namefix.CAVE, namefix.TABLE_END - namefix.CAVE),
             "wordwrap": (wordwrap.CAVE, len(wordwrap.CODE))}
    for name, (at, size) in caves.items():
        for start, end, what in regions:
            assert at + size <= start or end <= at, f"{name} cave {at:#x}+{size:#x} is in the {what} {start:#x}-{end:#x}"

    # 3. the published zip
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        prefix = f"luma/titles/{TITLE}/"
        bad = [n for n in names if not (n == prefix + "code.bin" or n.startswith(prefix + "romfs/"))]
        assert not bad, f"unexpected files in the zip: {bad[:5]}"
        code = z.read(prefix + "code.bin")
    assert len(code) == len(original), f"code.bin is {len(code):#x} bytes, the game's code {len(original):#x}"
    problems = luma.check(code, original, exheader)
    assert not problems, "\n".join(problems)
    print(f"ok: {os.path.basename(zpath)} is safe for Luma's LayeredFS "
          f"(payload {regions[0][0]:#x}-{regions[0][1]:#x}, path {regions[1][0]:#x}-{regions[1][1]:#x}, "
          f"hooks {', '.join(f'{k} {v + luma.BASE:#x}' for k, v in sorted(sites.items()))})")


if __name__ == "__main__":
    main()
