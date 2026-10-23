"""textrec.py - record every text the game shows (test builds with ExportOptions.test_text_log).

The build's logger (dq11tool/textlog.py) writes each pane text into a ring buffer in the
unused tail of the linear heap. TextRecorder switches it on and drains it from a background
thread (its own RPC socket), writing one JSON line per record to OUT/events.jsonl and a
screenshot of the screen as it was when new text arrived to OUT/shots/.

    rec = TextRecorder(session, out_dir); rec.start()
    ... drive the game ...
    rec.stop()                # final drain; rec.dropped > 0 means records were lost

Each event: seq, t (game seconds, HID clock = az.game_time), frame, pane (address), name
(pane name), phase ("raw" before the grammar pass, "resolved" after it - only logged when the
pass changed the text), start (index the text was written at), text (the string as the game
has it), shown (textcodec.decode_str form: {CR}, {the}, {v:..} ... visible), shot (file).

    python emutest/textrec.py OUT [SECONDS]     attach to the azctl session and record
"""
from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from dq11tool import textlog                       # noqa: E402
from dq11tool.textcodec import decode_str          # noqa: E402

TICKS = 268111856
UI_HEAP = 0x7F5144          # allocator 2's fallback heap object: +4 size, +0x10 base
CHUNK = 0x7000              # RPC requests carry at most 32 KB


def ring_address(read) -> int:
    size, = struct.unpack("<I", read(UI_HEAP + 4, 4))
    base, = struct.unpack("<I", read(UI_HEAP + 0x10, 4))
    if not (0x30000000 <= base < 0x40000000 and 0x100000 <= size < 0x800000):
        raise RuntimeError(f"UI heap not set up yet (base {base:#x}, size {size:#x})")
    return base + size


class TextRecorder:
    def __init__(self, session, out: str, shots: bool = True, poll: float = 0.01, settle: float = 0.25):
        from AzaharRPC import AzaharRPC
        self.s = session
        self.rpc = AzaharRPC(timeout=2, retries=2)
        self.out = out
        self.shots = shots
        self.poll = poll
        self.settle = settle            # screenshot once no new text came for this long (game s)
        self._pending = None            # game time of the last record not on a screenshot yet
        self.dropped = 0
        self.count = 0
        self.events: list[dict] = []
        self._stop = threading.Event()
        self._th = None
        self._lock = threading.RLock()     # one RPC socket: drain thread + callers' shot()
        os.makedirs(os.path.join(out, "shots"), exist_ok=True)
        self._fp = open(os.path.join(out, "events.jsonl"), "a", encoding="utf-8")

    # ---------------------------------------------------------------- setup
    def enable(self):
        rd = self.rpc.read_memory
        if rd(textlog.CTL, 4) is None:
            raise RuntimeError("RPC read failed")
        code = rd(textlog.CAVE, 8)
        if not code or not any(code):
            raise RuntimeError("this build has no text logger (build with test_text_log=True)")
        ring = ring_address(rd)
        self.ring, self.size = ring, textlog.RING_RESERVE - textlog.CACHE
        cur = struct.unpack("<5I", rd(textlog.CTL, 20))
        if cur[0] == ring:                      # already on (re-attached): continue from here
            self.rpos, self.seq = cur[2], cur[3]
            return
        self.rpc.write_memory(ring + self.size, bytes(textlog.CACHE))
        self.rpc.write_memory(textlog.CTL + 4, struct.pack("<4I", self.size, 0, 0, textlog.HID_TICK))
        self.rpc.write_memory(textlog.CTL, struct.pack("<I", ring))
        self.rpos, self.seq = 0, 0

    def start(self):
        self.enable()
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()
        return self

    def stop(self):
        self._stop.set()
        if self._th:
            self._th.join(5)
        self.drain()
        if self._pending is not None and self.shots:
            self._pending = None
            self.shot("end")
        self._fp.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.drain()
            except Exception as e:             # emulator gone / RPC timeout: keep trying
                print("textrec:", e, flush=True)
                time.sleep(0.5)
            time.sleep(self.poll)

    # ---------------------------------------------------------------- reading
    def _read(self, off: int, n: int) -> bytes:
        out = b""
        while n > 0:
            k = min(n, CHUNK)
            d = self.rpc.read_memory(self.ring + off, k)
            if d is None:
                raise RuntimeError("ring read failed")
            out += d
            off, n = off + k, n - k
        return out

    def _records(self, start: int, end: int, first_seq: int) -> list[dict] | None:
        """Records from offset start up to end; None if they do not continue first_seq."""
        if start <= end:
            spans = [(start, self._read(start, end - start))]
        else:
            spans = [(start, self._read(start, self.size - start)), (0, self._read(0, end))]
        out, want = [], first_seq
        for _, data in spans:
            o = 0
            while o + 2 <= len(data):
                sz, = struct.unpack_from("<H", data, o)
                if sz == 0:                     # wrapped: continue at offset 0 (next span)
                    break
                if sz < textlog.HEADER or o + sz > len(data):
                    return None
                r = textlog.decode(data[o:o + sz])
                if r["seq"] != want:
                    return None
                out.append(r)
                want += 1
                o += sz
        return out

    def drain(self):
        with self._lock:
            pos, seq = struct.unpack("<II", self.rpc.read_memory(textlog.CTL + 8, 8))
            if seq == self.seq:
                self._settled_shot()
                return
            # the logger counts a record (seq) before it publishes the write offset (pos), so
            # read up to pos and continue from the last record found
            recs = self._records(self.rpos, pos, self.seq) if seq - self.seq < 20000 else None
            if recs is None:
                # the writer lapped us: records since the last wrap start at offset 0
                recs = [r for r in (self._records_from_zero(pos) or []) if r["seq"] >= self.seq]
                lost = (recs[0]["seq"] if recs else seq) - self.seq
                self.dropped += lost
                self._emit({"lost": lost, "t": None})
                print(f"textrec: LOST {lost} records (ring overrun)", flush=True)
            self.rpos = pos
            if recs:
                self.seq = recs[-1]["seq"] + 1
            if not recs:
                return
            for r in recs:
                self._emit({"seq": r["seq"], "t": round(r["tick"] / TICKS, 3), "frame": r["frame"],
                            "pane": f"{r['pane']:08x}", "name": r["name"],
                            "phase": "resolved" if r["phase"] else "raw", "start": r["start"],
                            "text": r["text"], "shown": decode_str(r["text"])})
            self._pending = recs[-1]["tick"] / TICKS

    def now(self) -> float:
        return struct.unpack("<Q", self.rpc.read_memory(textlog.HID_TICK, 8))[0] / TICKS

    def shot(self, tag: str = "") -> str | None:
        """Screenshot now (both screens) -> shots/, logged as a "shot" event."""
        with self._lock:
            return self._shot(tag)

    def _shot(self, tag: str) -> str | None:
        t = self.now()
        name = os.path.join("shots", f"{t:09.3f}{'_' + tag if tag else ''}.png")
        for _ in range(20):                 # "Busy" while another client takes one
            try:
                if self.rpc.save_screenshot(os.path.join(self.out, name)):
                    break
            except Exception as e:
                if "Busy" not in str(e):
                    return None
            time.sleep(0.05)
        else:
            return None
        self._emit({"shot": name.replace(os.sep, "/"), "t": round(t, 3), "after_seq": self.seq - 1, "tag": tag})
        return name

    def _settled_shot(self):
        if self.shots and self._pending is not None and self.now() - self._pending >= self.settle:
            self._pending = None
            self.shot()

    def _records_from_zero(self, end: int):
        data = self._read(0, end)
        out, o = [], 0
        while o + textlog.HEADER <= len(data):
            sz, = struct.unpack_from("<H", data, o)
            if sz < textlog.HEADER:
                break
            out.append(textlog.decode(data[o:o + sz]))
            o += sz
        return out

    def _emit(self, ev: dict):
        self.events.append(ev)
        self.count += "seq" in ev
        self._fp.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._fp.flush()


def load(out: str) -> list[dict]:
    with open(os.path.join(out, "events.jsonl"), encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


if __name__ == "__main__":
    import azctl
    out = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    s = azctl.Session()
    rec = TextRecorder(s, out).start()
    s.wait_game(s.game_time() + secs)
    rec.stop()
    print(rec.count, "records,", rec.dropped, "lost ->", out)
