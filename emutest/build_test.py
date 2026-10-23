"""build_test.py [--no-hook] [--name NAME] - translation as an Azahar mod (fast test build).

Writes the changed RomFS files and the decompressed code.bin into
_emutest/azahar/user/load/mods/<title id>/ (Azahar's LayeredFS), so tests boot the original
Japanese ROM with the translation applied. With the input hook (default) code.bin also gets
the RPC input mailbox (dq11tool/testhook.py).
"""
import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
import az  # noqa: E402
from dq11tool.export import ExportOptions, collect_changes   # noqa: E402
from dq11tool.project import Project                         # noqa: E402

MODS = os.path.join(az.USER, "load", "mods", "0004000000199200")


def write_mod(romfs_repl: dict, code: bytes | None, dest: str = MODS):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for path, data in romfs_repl.items():
        p = os.path.join(dest, "romfs", path.lstrip("/").replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fp:
            fp.write(data)
    if code is not None:
        os.makedirs(os.path.join(dest, "exefs"), exist_ok=True)
        with open(os.path.join(dest, "exefs", "code.bin"), "wb") as fp:
            fp.write(code)


def write_code(code: bytes, dest: str = MODS):
    """Replace only the mod's code.bin (bisecting)."""
    os.makedirs(os.path.join(dest, "exefs"), exist_ok=True)
    with open(os.path.join(dest, "exefs", "code.bin"), "wb") as fp:
        fp.write(code)


def original(path: str) -> bytes:
    """A RomFS file of the Japanese game (workspace/extracted)."""
    with open(os.path.join(REPO, "workspace", "extracted", "romfs", path.lstrip("/")), "rb") as fp:
        return fp.read()


def build(hook: bool = True, only=None, patches: dict | None = None, **opt) -> dict:
    """only: optional predicate on RomFS paths; other changed files are left out (tests).
    patches: {RomFS path: function(bytes) -> bytes}, applied to the exported file (or the
    original when the export does not change it): test-only data (testroom.py, battleparam.py)."""
    t0 = time.time()
    project = Project(os.path.join(REPO, "workspace"))
    opts = ExportOptions(make_3ds=False, make_layeredfs=False, test_input_hook=hook, **opt)
    romfs, exefs, report, code = collect_changes(project, opts,
                                                 lambda s, i, n: None)
    if code is None and hook:
        raise RuntimeError("no code produced")
    if only is not None:
        romfs = {p: d for p, d in romfs.items() if only(p)}
    for path, fn in (patches or {}).items():
        romfs[path] = fn(romfs[path] if path in romfs else original(path))
    write_mod(romfs, code)
    return {"files": len(romfs), "applied": report.applied, "skipped": len(report.skipped),
            "warnings": len(report.warnings), "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-hook", action="store_true")
    a = ap.parse_args()
    print(build(hook=not a.no_hook))
