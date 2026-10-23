"""Backward LZ77 (BLZ) codec. Uses the native helper when available, else pure Python."""
from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_NATIVE_DIR = os.path.join(_HERE, "native")
_LIBNAME = "blz.dll" if sys.platform == "win32" else "libblz.so"
_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib or None
    path = os.path.join(_NATIVE_DIR, _LIBNAME)
    src = os.path.join(_NATIVE_DIR, "blz.c")
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(src):
        try:
            subprocess.run(["gcc", "-O2", "-shared", "-o", path, src],
                           check=True, capture_output=True)
        except Exception:
            pass
    try:
        lib = ctypes.CDLL(path)
        lib.blz_decompress.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        lib.blz_decompress.restype = ctypes.c_int
        lib.blz_compress.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        lib.blz_compress.restype = ctypes.c_int64
        _lib = lib
    except OSError:
        _lib = False
    return _lib or None


def decompress(data: bytes) -> bytes:
    tb, extra = struct.unpack_from("<II", data, len(data) - 8)
    out_size = len(data) + extra
    lib = _load()
    if lib:
        buf = ctypes.create_string_buffer(out_size)
        rc = lib.blz_decompress(data, len(data), buf, out_size)
        if rc != 0:
            raise ValueError(f"BLZ decompress failed ({rc})")
        return buf.raw
    return _py_decompress(data)


def compress(data: bytes) -> bytes | None:
    """Return BLZ-compressed bytes, or None if the data doesn't compress."""
    lib = _load()
    if lib:
        buf = ctypes.create_string_buffer(len(data) + 16)
        n = lib.blz_compress(data, len(data), buf, len(data) + 16)
        if n < 0:
            raise MemoryError("BLZ compress failed")
        return buf.raw[:n] if n > 0 else None
    return None  # pure-python fallback: store uncompressed


def _py_decompress(data: bytes) -> bytes:
    n = len(data)
    tb, extra = struct.unpack_from("<II", data, n - 8)
    top, bottom = tb & 0xFFFFFF, tb >> 24
    out = bytearray(data) + bytearray(extra)
    dst = len(out)
    src = n - bottom
    end = n - top
    while src > end:
        src -= 1
        flag = out[src]
        for i in range(8):
            if not (flag << i) & 0x80:
                src -= 1
                dst -= 1
                out[dst] = out[src]
            else:
                hi = out[src - 1]
                lo = out[src - 2]
                src -= 2
                tok = hi << 8 | lo
                off = (tok & 0xFFF) + 3
                ln = (tok >> 12) + 3
                for _ in range(ln):
                    dst -= 1
                    out[dst] = out[dst + off]
            if src <= end:
                break
    return bytes(out)
