"""setup_azahar.py [AZAHAR_DIR] - create the portable test emulator in _emutest/azahar.

Copies an Azahar install (default C:\\azahar) to _emutest/azahar, adds an empty user/ folder
(Azahar then keeps its config, saves and logs there instead of %APPDATA%), lets it write
its default config once, applies the test profile (az.SETTINGS) and extracts the Japanese
.code to _emutest/code_jp.bin for armdis.py. The user's own Azahar install and profile are
never modified. Never run azahar.exe --help: it opens a dialog on the user's screen.
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az  # noqa: E402


def main(src: str):
    if not os.path.isfile(os.path.join(src, "azahar.exe")):
        raise SystemExit(f"no azahar.exe in {src}")
    if not os.path.isdir(az.AZ):
        print("copying", src, "->", az.AZ)
        shutil.copytree(src, az.AZ)
    os.makedirs(az.USER, exist_ok=True)
    os.makedirs(az.SAVES, exist_ok=True)
    if not os.path.isfile(az.INI):
        print("writing the default config (Azahar opens without focus for a few seconds)")
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4          # SW_SHOWNOACTIVATE
        p = subprocess.Popen([az.EXE], startupinfo=si)
        for _ in range(40):
            if os.path.isfile(az.INI):
                break
            time.sleep(0.5)
        time.sleep(2)
        p.kill()
    az.configure()
    code = os.path.join(az.ROOT, "code_jp.bin")
    if not os.path.isfile(code) and os.path.isfile(az.JP_ROM):
        sys.path.insert(0, az.REPO)
        from dq11tool import blz
        from dq11tool.ctr.formats import open_image
        f, _, ncch = open_image(az.JP_ROM)
        try:
            c = ncch.exefs().read(".code")
            if ncch.exheader()[0x0D] & 1:
                c = blz.decompress(c)
        finally:
            f.close()
        open(code, "wb").write(c)
        print("extracted", code)
    print("ready:", az.EXE)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\azahar")
