"""Minimal GDB remote protocol client for Azahar's GDB stub (start the emulator with
settings=az.GDB). Breakpoints and watchpoints without a gdb install:

    g = Gdb(); g.cont()                   # the stub halts before the first instruction
    ...
    g.interrupt(); g.watch(0x33e72a32, 2) # write watchpoint
    g.cont(); stop = g.wait()             # -> stop reply, then g.regs()["pc"], g.regs()["lr"]
"""
import socket
import struct
import time

import az

REG_NAMES = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]


class Gdb:
    def __init__(self, port: int = az.GDB_PORT, timeout: float = 60):
        end = time.time() + timeout
        while True:
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.2)
        self.buf = b""
        self.cmd("?")

    # --- packets
    def _send(self, body: str):
        data = body.encode("latin-1")
        self.s.sendall(b"$" + data + b"#" + f"{sum(data) & 0xFF:02x}".encode())

    def _recv(self, timeout: float | None = 5) -> str:
        self.s.settimeout(timeout)
        while True:
            i = self.buf.find(b"$")
            j = self.buf.find(b"#", i)
            if i >= 0 and j >= 0 and len(self.buf) >= j + 3:
                body = self.buf[i + 1:j]
                self.buf = self.buf[j + 3:]
                self.s.sendall(b"+")
                return body.decode("latin-1")
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("gdb stub closed")
            self.buf += chunk

    def cmd(self, body: str, timeout: float | None = 5) -> str:
        self._send(body)
        return self._recv(timeout)

    # --- control
    def cont(self):
        self._send("c")

    def wait(self, timeout: float | None = None) -> str | None:
        """Wait for a stop reply (breakpoint/watchpoint hit); None on timeout."""
        try:
            return self._recv(timeout)
        except socket.timeout:
            return None

    def interrupt(self) -> str:
        self.s.sendall(b"\x03")
        return self._recv(10)

    def regs(self) -> dict:
        h = self.cmd("g")
        vals = struct.unpack("<16I", bytes.fromhex(h[:128]))
        return dict(zip(REG_NAMES, vals))

    def read(self, addr: int, size: int) -> bytes:
        return bytes.fromhex(self.cmd(f"m{addr:x},{size:x}"))

    def stack(self, n: int = 64) -> list[int]:
        sp = self.regs()["sp"]
        return list(struct.unpack(f"<{n}I", self.read(sp, 4 * n)))

    def watch(self, addr: int, size: int = 4, kind: int = 2) -> str:
        """kind 2 = write, 3 = read, 4 = access watchpoint; 0/1 = breakpoint."""
        return self.cmd(f"Z{kind},{addr:x},{size:x}")

    def unwatch(self, addr: int, size: int = 4, kind: int = 2) -> str:
        return self.cmd(f"z{kind},{addr:x},{size:x}")

    def brk(self, addr: int) -> str:
        return self.watch(addr, 4, 0)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass
