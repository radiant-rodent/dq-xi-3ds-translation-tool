"""autofittest.py - dq11tool/autofit.py: the embedded code matches its source, fits its cave,
and the patch applies to the original executable (hook site and cave as expected).

    python emutest/autofittest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dq11tool import autofit, textlog   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    for k, v in autofit.assemble_all().items():
        assert v == getattr(autofit, k), f"{k} is stale: python -m dq11tool.autofit --write"
    from dq11tool import bflyt
    assert (autofit.MARKER, autofit.MARKER_MULTI) == (bflyt.FIT_MARKER, bflyt.FIT_MARKER_MULTI)
    end = autofit.CAVE + len(autofit.CODE)
    from dq11tool import grammar_code
    assert end <= grammar_code.CAVE, f"autofit cave ends at {end:#x}, past grammar_code.CAVE"
    from dq11tool import namefix
    assert end <= namefix.CAVE, f"autofit cave ends at {end:#x}, past namefix.CAVE"
    assert namefix.CAVE + len(namefix.CODE) <= namefix.TABLE <= namefix.TABLE_END <= grammar_code.CAVE
    assert not (autofit.CAVE <= textlog.CAVE < end), "textlog.CAVE inside the autofit cave"
    from dq11tool import testhook, wordwrap, fontnarrow
    assert wordwrap.CAVE + len(wordwrap.CODE) <= autofit.SCALE_AT
    assert autofit.SCALE_AT + len(autofit.SCALE_CODE) <= autofit.SCALE_END <= textlog.CAVE
    assert testhook.HOOK + len(testhook.CODE) <= autofit.REMAP_AT
    assert autofit.REMAP_AT + len(autofit.REMAP_CODE) <= autofit.REMAP_END
    assert grammar_code.CAVE + len(grammar_code.CODE) <= autofit.BITMAP_AT
    assert autofit.BITMAP_AT + 32 <= wordwrap.CAVE
    assert autofit.LEVELS == len(fontnarrow.LEVELS)
    assert fontnarrow.code(0xFF, len(fontnarrow.LEVELS)) < 0xE200 + 0x400 + 0x100
    code = open(os.path.join(os.path.dirname(HERE), "_emutest", "code_jp.bin"), "rb").read()
    out, problems = autofit.patch(code)
    assert not problems, problems
    again, problems = autofit.patch(out)
    assert again == out and not problems, "patch is not idempotent"
    print(f"ok: {len(autofit.CODE)} bytes at {autofit.CAVE:#x}..{end:#x}")


if __name__ == "__main__":
    main()
