"""azctl.py - drive a running Azahar test session step by step (one command per call).

  azctl.py start [--save NAME] [--rom PATH] [--movie CTM] [--speed PCT]  boot (JP ROM + test mod by default)
  azctl.py press A [A B ...]                press buttons one after another
  azctl.py hold BTN SECONDS
  azctl.py walk U|D|L|R|UL|UR|DL|DR SECONDS circle pad
  azctl.py wait SECONDS                     let game time pass
  azctl.py shot [NAME]                      screenshot -> runs/ctl/NAME.png (both screens)
  azctl.py mash A SECONDS [EVERY]           press repeatedly (dialogue)
  azctl.py status                           game time, hook alive, crash line
  azctl.py stop
Every command prints the game time and a crash line if the log shows one.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az  # noqa: E402

STATE = os.path.join(az.ROOT, "runs", "ctl", "session.json")
OUT = os.path.join(az.ROOT, "runs", "ctl")
JP_ROM = az.JP_ROM


class Session(az.Azahar):
    """Attach to the emulator started by `start` (pid in session.json)."""
    def __init__(self):
        st = json.load(open(STATE))
        super().__init__(st["rom"])
        self.pid = st["pid"]
        from AzaharRPC import AzaharRPC
        self.rpc = AzaharRPC(timeout=2, retries=2)
        self.gt = 0.0
        self.proc = None

    def alive(self):
        r = subprocess.run(["tasklist", "/FI", f"PID eq {self.pid}", "/NH"], capture_output=True, text=True)
        return str(self.pid) in r.stdout

    def crashed(self):
        if not self.alive():
            return "emulator not running"
        for line in self.log(tail=200000).splitlines():
            if az.CRASH_RE.search(line) and "ReadBlock" not in line:
                return line.strip()
        return None


def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "seq":
        # seq "walk U 3" "shot a" ...: several commands in one process; shots go into seq.png
        shots = []
        for step in args:
            parts = step.split()
            main(parts)
            if parts[0] == "shot":
                shots.append(os.path.join(OUT, (parts[1] if len(parts) > 1 else "") + ".png"))
        if shots:
            az.sheet(shots, os.path.join(OUT, "seq.png"), cols=min(4, len(shots)), scale=0.75)
        return
    os.makedirs(OUT, exist_ok=True)
    if cmd == "start":
        rom, save, movie = JP_ROM, "__keep__", None
        if "--rom" in args:
            rom = args[args.index("--rom") + 1]
        if "--save" in args:
            save = args[args.index("--save") + 1]
        if "--movie" in args:
            movie = args[args.index("--movie") + 1]
        settings = {}
        if "--speed" in args:       # emulation speed in % (0 = unlimited, the default); 100 for walking
            settings["frame_limit"] = args[args.index("--speed") + 1]
        e = az.Azahar(rom, movie=movie, save=save, workdir=OUT, settings=settings)
        e.start()
        json.dump({"pid": e.proc.pid, "rom": rom}, open(STATE, "w"))
        print("started pid", e.proc.pid)
        if "--boot" in args:
            main(["boot", args[args.index("--boot") + 1]])
        return
    s = Session()
    c = None
    if cmd == "stop":
        subprocess.run(["taskkill", "/PID", str(s.pid), "/F"], capture_output=True)
        print("stopped")
        return
    if cmd == "press":
        for b in args:
            c = s.press(b) or c
    elif cmd == "hold":
        c = s.hold(args[0], float(args[1]))
    elif cmd == "walk":
        c = s.walk(args[0], float(args[1]))
    elif cmd == "wait":
        c = s.wait_game(s.game_time() + float(args[0]))
    elif cmd == "mash":
        every = float(args[2]) if len(args) > 2 else 0.6
        end = s.game_time() + float(args[1])
        while s.game_time() < end and not c:
            c = s.press(args[0], after=every)
    elif cmd == "shot":
        name = args[0] if args else time.strftime("%H%M%S")
        p = os.path.join(OUT, name + ".png")
        s.shot(p)
        print(p)
    elif cmd == "boot":
        # title -> Continue -> slot (cursor default) -> recap/greeting skipped with B
        # (state-tolerant: A only ever confirms, B closes the recap and the priest's lines)
        s.wait_game(6)
        while s.game_time() < 40:
            s.press("A", after=1.4)
        while s.game_time() < 65:
            s.press("B", after=0.8)
    elif cmd == "status":
        print("hook alive:", s.hook_alive())
    print(f"t={s.game_time():.1f}", ("CRASH: " + (c or s.crashed())) if (c or s.crashed()) else "")


if __name__ == "__main__":
    main(sys.argv[1:])
