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
    assert autofit.assemble() == autofit.CODE, "CODE is stale: re-assemble autofit.SOURCE"
    from dq11tool import bflyt
    assert (autofit.MARKER, autofit.MARKER_MULTI) == (bflyt.FIT_MARKER, bflyt.FIT_MARKER_MULTI)
    end = autofit.CAVE + len(autofit.CODE)
    from dq11tool import grammar_code
    assert end <= grammar_code.CAVE, f"autofit cave ends at {end:#x}, past grammar_code.CAVE"
    from dq11tool import namefix
    assert end <= namefix.CAVE, f"autofit cave ends at {end:#x}, past namefix.CAVE"
    assert namefix.CAVE + len(namefix.CODE) <= namefix.TABLE <= namefix.TABLE_END <= grammar_code.CAVE
    assert not (autofit.CAVE <= textlog.CAVE < end), "textlog.CAVE inside the autofit cave"
    code = open(os.path.join(os.path.dirname(HERE), "_emutest", "code_jp.bin"), "rb").read()
    out, problems = autofit.patch(code)
    assert not problems, problems
    again, problems = autofit.patch(out)
    assert again == out and not problems, "patch is not idempotent"
    print(f"ok: {len(autofit.CODE)} bytes at {autofit.CAVE:#x}..{end:#x}")


if __name__ == "__main__":
    main()
