"""Headless-ish Azahar test harness for the DQ XI translation.

Portable Azahar lives in _emutest/azahar (its own user/ folder, never the user's install;
see emutest/setup_azahar.py and docs/AGENT_GUIDE.md).
The window is opened without activation (no focus stealing, no simulated input); all
control goes through input movies (.ctm) and the Azahar RPC server (memory + screenshots).

    from az import Azahar, Movie
    m = Movie(); m.press("A", at=6.0) ...
    with Azahar(rom, movie=m, save="cobblestone_start") as emu:
        emu.wait_until(30); emu.shot("x.png")
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/emutest
REPO = os.path.dirname(HERE)
# machine-local test data (portable Azahar, saves, run output); gitignored
ROOT = os.environ.get("DQ11_EMUTEST") or os.path.join(REPO, "_emutest")
JP_ROM = os.environ.get("DQ11_ROM") or os.path.join(REPO, "DQXI.3ds")
AZ = os.path.join(ROOT, "azahar")
EXE = os.path.join(AZ, "azahar.exe")
USER = os.path.join(AZ, "user")
INI = os.path.join(USER, "config", "qt-config.ini")
LOG = os.path.join(USER, "log", "azahar_log.txt")
SAVES = os.path.join(ROOT, "saves")
TITLE = 0x0004000000199200
SAVE_DIR = os.path.join(USER, "sdmc", "Nintendo 3DS", "0" * 32, "0" * 32, "title", "00040000", "00199200", "data")
sys.path.insert(0, os.path.join(AZ, "scripting"))

# test profile: Vulkan, native resolution, Old 3DS (the stricter memory budget), unlimited
# speed, no vsync/audio, RPC on, no dialogs
SETTINGS = {
    "graphics_api": "2", "resolution_factor": "1", "frame_limit": "0", "use_vsync": "false",
    "is_new_3ds": "false", "enable_rpc_server": "true", "output_type": "1",
    "confirmClose": "false", "firstStart": "false", "saveStateWarning": "false",
    "pauseWhenInBackground": "false", "async_shader_compilation": "false",
    "init_clock": "1",          # fixed clock: movies replay deterministically
    "region_value": "0", "check_for_update_on_start": "false",
}


# GDB stub (emutest/gdb.py): with it on, the emulator waits for a debugger before the first instruction
GDB_PORT = 24689
GDB = {"use_gdbstub": "true", "gdbstub_port": str(GDB_PORT)}
GDB_OFF = {"use_gdbstub": "false"}


# guest crash signatures in the Azahar log
CRASH_RE = re.compile(r"(Data|Prefetch) Abort|Unmapped (Read|Write)|Undefined instruction|svcBreak|"
                      r"ARM11 caught|Fatal|Unhandled|Invalid memory|Unknown instruction", re.I)


def configure(extra: dict | None = None):
    s = dict(SETTINGS, **(extra or {}))
    # experiments: AZ_SETTINGS="graphics_api=1,resolution_factor=2"
    s.update(kv.split("=", 1) for kv in os.environ.get("AZ_SETTINGS", "").split(",") if "=" in kv)
    txt = open(INI, encoding="utf-8").read()
    for k, v in s.items():
        txt = re.sub(rf"(?m)^({re.escape(k)})\\default=.*$", r"\1\\default=false", txt)
        txt = re.sub(rf"(?m)^({re.escape(k)})=.*$", lambda m: f"{m.group(1)}={v}", txt)
    open(INI, "w", encoding="utf-8").write(txt)


def install_save(name: str | None):
    """Copy _emutest/saves/<name> (game save: 00000001/, 00000001.metadata) into the sdmc."""
    if os.path.isdir(SAVE_DIR):
        shutil.rmtree(SAVE_DIR)
    if name:
        shutil.copytree(os.path.join(SAVES, name), SAVE_DIR)


def export_save(name: str):
    dst = os.path.join(SAVES, name)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(SAVE_DIR, dst)


# ----------------------------------------------------------------------------- movies
BTN = dict(A=1, B=2, SELECT=4, START=8, RIGHT=16, LEFT=32, UP=64, DOWN=128, R=256, L=512, X=1024, Y=2048)
CIRCLE = {"CU": (0, 156), "CD": (0, -156), "CL": (-156, 0), "CR": (156, 0),
          "CUR": (110, 110), "CUL": (-110, 110), "CDR": (110, -110), "CDL": (-110, -110)}
RATE = 234          # HID pad updates per emulated second; each takes a pad+circle and a touch entry


class Movie:
    """Input movie. Times are emulated seconds from boot. Entries per HID update:
    type 0 (u16 buttons, s16 circle x, s16 circle y) then type 1 (u16 x, u16 y, u8 touching)."""
    def __init__(self):
        self.events: list[tuple[float, float, int, tuple | None, tuple | None]] = []
        self.t = 0.0

    def hold(self, buttons: str, at: float, dur: float):
        m, c, touch = 0, None, None
        for nm in filter(None, buttons.upper().split("+")):
            if nm in CIRCLE:
                c = CIRCLE[nm]
            elif nm.startswith("T") and "/" in nm:          # T120/200: touch bottom screen at x,y
                x, y = nm[1:].split("/")
                touch = (int(x), int(y))
            else:
                m |= BTN[nm]
        self.events.append((at, dur, m, c, touch))
        self.t = max(self.t, at + dur)
        return self

    def press(self, buttons: str, at: float | None = None, dur: float = 0.12, gap: float = 0.5):
        at = self.t + gap if at is None else at
        return self.hold(buttons, at, dur)

    def spec(self, s: str):
        """"t:dur:BTN+BTN,..." (BTN: A B X Y L R START SELECT UP DOWN LEFT RIGHT, CU/CD/CL/CR/CUR..
        circle pad, Tx/y touch)"""
        for it in filter(None, s.replace(" ", "").split(",")):
            t, d, b = it.split(":")
            self.hold(b, float(t), float(d))
        return self

    def write(self, path: str, tail: float = 3600.0):
        n = int((self.t + tail) * RATE)
        pad = [[0, 0, 0] for _ in range(int((self.t + 1) * RATE) + 1)]
        tch = {}
        for s, d, m, c, touch in self.events:
            for i in range(int(s * RATE), min(len(pad), int((s + d) * RATE) + 1)):
                pad[i][0] |= m
                if c:
                    pad[i][1], pad[i][2] = c
                if touch:
                    tch[i] = touch
        body = bytearray()
        for i in range(n):
            b, cx, cy = pad[i] if i < len(pad) else (0, 0, 0)
            body += struct.pack("<BHhh", 0, b, cx, cy)
            t = tch.get(i)
            body += struct.pack("<BHHBx", 1, t[0], t[1], 1) if t else struct.pack("<BHHBx", 1, 0, 0, 0)
        hdr = b"CTM" + struct.pack("<Q", TITLE) + bytes(20) + struct.pack("<QQ", 946681277, 0x1234)
        hdr += b"emutest".ljust(32, bytes(1)) + struct.pack("<IQ", 0, 2 * n)
        with open(path, "wb") as fp:
            fp.write(hdr + bytes(256 - len(hdr)) + body)


# ----------------------------------------------------------------------------- emulator
def kill_test_emulators():
    """Stop running instances of the *test* emulator (matched by executable path; the user's
    own Azahar is never touched)."""
    ps = ("Get-Process azahar -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '%s' } "
          "| Stop-Process -Force" % EXE.replace("'", "''"))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


# ----------------------------------------------------------------------------- emulator
class Azahar:
    def __init__(self, rom: str, movie: Movie | str | None = None, save: str | None = "__keep__",
                 workdir: str | None = None, settings: dict | None = None):
        """settings: extra qt-config.ini values for this run (e.g. az.GDB to enable the GDB stub)."""
        self.rom, self.movie, self.save = rom, movie, save
        self.settings = settings
        self.workdir = workdir or os.path.join(ROOT, "runs", "last")
        self.proc = None
        self.rpc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def start(self):
        os.makedirs(self.workdir, exist_ok=True)
        kill_test_emulators()       # one instance only: the RPC port is fixed
        configure(dict(GDB_OFF, **(self.settings or {})))
        if self.save != "__keep__":
            install_save(self.save)
        args = [EXE]
        if self.movie is not None:
            mp = self.movie
            if isinstance(mp, Movie):
                mp = os.path.join(self.workdir, "input.ctm")
                self.movie.write(mp)
            args += ["-p", mp]
        args.append(self.rom)
        if os.path.exists(LOG):
            os.remove(LOG)
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4          # SW_SHOWNOACTIVATE: never takes focus; minimizing breaks rendering
        self.proc = subprocess.Popen(args, startupinfo=si, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.t0 = self.last = time.time()
        self.gt = 0.0
        from AzaharRPC import AzaharRPC
        self.rpc = AzaharRPC(timeout=2, retries=2)
        self.attach()
        return self

    def attach(self, timeout: float = 60):
        """Select the game process for RPC memory access (reads before that are invalid)."""
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("emulator exited during boot")
            try:
                for pid, (tid, name) in self.rpc.process_list().items():
                    if tid == TITLE:
                        self.rpc.set_process(pid)
                        return pid
            except Exception:
                pass
            time.sleep(0.2)
        raise RuntimeError("game process not found")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    # --- state
    def log(self, tail: int = 0) -> str:
        """The emulator log (the last `tail` bytes only, if given: a crash loop can grow it
        by megabytes per second)."""
        try:
            with open(LOG, "rb") as fp:
                if tail:
                    fp.seek(0, 2)
                    fp.seek(max(0, fp.tell() - tail))
                return fp.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def first_crash(self) -> str | None:
        """First crash line in the log (reads the log in chunks)."""
        try:
            with open(LOG, "rb") as fp:
                for raw in fp:
                    line = raw.decode("utf-8", "replace")
                    if CRASH_RE.search(line) and "ReadBlock" not in line:
                        return line.strip()
        except OSError:
            pass
        return None

    def crashed(self) -> str | None:
        """Crash signature from the log (guest data abort / prefetch abort / unmapped access)."""
        if self.proc.poll() is not None:
            return f"emulator exited ({self.proc.returncode})"
        for line in self.log(tail=200000).splitlines():
            if CRASH_RE.search(line) and "ReadBlock" not in line:
                return line.strip()
        return None

    def perf(self):
        try:
            return self.rpc.perf_stats()
        except Exception:
            return None

    def game_time(self) -> float:
        """Emulated seconds since boot: tick of the latest HID pad update (HID shared memory
        is mapped at 0x10002000 in DQ XI; ARM11 ticks at 268111856 Hz). Movie inputs are
        consumed one per HID update, so this is the movie clock."""
        try:
            d = self.rpc.read_memory(0x10002000, 8)
        except Exception:
            d = None
        if d:
            self.gt = struct.unpack("<Q", d)[0] / 268111856
        return self.gt

    def perf_reset(self):
        try:
            return self.rpc.perf_stats(reset=True)
        except Exception:
            return None

    def wait_game(self, t: float, poll: float = 0.02) -> str | None:
        """Run until emulated time t; returns a crash description or None. The crash check
        (log scan) runs every 0.5 s real; the clock is polled often because the emulator
        runs ~12x faster than real time."""
        last = 0.0
        while self.game_time() < t:
            if time.time() - last > 0.5:
                last = time.time()
                c = self.crashed()
                if c:
                    return c
            time.sleep(poll)
        return None

    # --- live input (test builds with dq11tool/testhook.py)
    MAILBOX = 0x839F00

    def hook_alive(self) -> bool:
        a = self.read(self.MAILBOX + 16, 4)
        time.sleep(0.05)
        b = self.read(self.MAILBOX + 16, 4)
        return bool(a and b and a != b)

    def _mask(self, buttons: str) -> int:
        m = 0
        for nm in filter(None, buttons.upper().split("+")):
            m |= BTN[nm]
        return m

    def press(self, buttons: str, frames: int = 2, after: float = 0.4):
        """Press for exactly `frames` game frames (the hook releases it), then let `after`
        game seconds pass."""
        self.write(self.MAILBOX + 20, struct.pack("<II", self._mask(buttons), frames))
        end = time.time() + 5
        while time.time() < end and struct.unpack("<I", self.read(self.MAILBOX + 24, 4))[0]:
            time.sleep(0.005)
        return self.wait_game(self.game_time() + after)

    def hold(self, buttons: str, seconds: float):
        m = self._mask(buttons)
        self.write(self.MAILBOX, struct.pack("<II", m, m))
        c = self.wait_game(self.game_time() + seconds)
        self.write(self.MAILBOX, struct.pack("<I", 0))
        return c

    def stick(self, x: int, y: int, seconds: float):
        """Circle pad (-156..156; y up) for `seconds` game seconds, then centred."""
        self.write(self.MAILBOX + 8, struct.pack("<hhI", x, y, 1))
        c = self.wait_game(self.game_time() + seconds)
        self.write(self.MAILBOX + 8, struct.pack("<hhI", 0, 0, 0))
        return c

    def walk(self, direction: str, seconds: float):
        dx, dy = {"U": (0, 1), "D": (0, -1), "L": (-1, 0), "R": (1, 0), "UL": (-1, 1), "UR": (1, 1),
                  "DL": (-1, -1), "DR": (1, -1)}[direction.upper()]
        k = 156 if dx == 0 or dy == 0 else 110
        return self.stick(dx * k, dy * k, seconds)

    # --- memory / screen
    def read(self, addr: int, size: int) -> bytes:
        return self.rpc.read_memory(addr, size)

    def write(self, addr: int, data: bytes) -> bool:
        return self.rpc.write_memory(addr, data)

    def shot(self, path: str, bottom: bool = False) -> bool:
        """Both screens as the window lays them out (default layout: top over bottom)."""
        for _ in range(20):         # "Busy": another RPC client (textrec) is taking one
            try:
                return self.rpc.save_screenshot(path)
            except Exception as e:
                if "Busy" not in str(e):
                    print("screenshot failed:", e)
                    return False
            time.sleep(0.05)
        print("screenshot failed: busy")
        return False


def sheet(paths: list[str], out: str, cols: int = 4, scale: float = 0.5):
    from PIL import Image
    ims = [Image.open(p) for p in paths if os.path.exists(p)]
    if not ims:
        return
    w, h = int(ims[0].width * scale), int(ims[0].height * scale)
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (w * cols, h * rows), "black")
    for i, im in enumerate(ims):
        s.paste(im.resize((w, h)), ((i % cols) * w, (i // cols) * h))
    s.save(out)
