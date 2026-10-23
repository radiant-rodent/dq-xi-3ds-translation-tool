"""packls.py PACK [PATTERN] - list the members of a game .pack (name, size, first bytes)."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dq11tool.pack import Pack  # noqa: E402

p = Pack.parse(open(sys.argv[1], "rb").read())
pat = re.compile(sys.argv[2]) if len(sys.argv) > 2 else None
for e in p.entries:
    if pat is None or pat.search(e.name):
        print(f"{e.name:48s} {len(e.data):8d} {e.data[:16].hex(' ')}")
