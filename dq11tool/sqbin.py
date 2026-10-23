"""Compiled Squirrel scripts (.cnut, Squirrel 3.0 as used by the game): full parse, rebuild and
disassembly. cnut.py only finds string literals; this module models the function tree so tests
can change code (dq11tool/testroom.py).

Stream (little endian, SQInteger = 4 bytes, SQChar = 1 byte):
    u16 0xFAFA, 'RIQS', u32 sizeof(SQChar), function, 'LIAT'
    (Squirrel 3.1 adds u32 sizeof(SQInteger), u32 sizeof(SQFloat) after the char size and
    writes varparams as u32; its opcodes are renumbered too, so its compiler cannot be used:
    the game's VM uses the Squirrel 2.2 opcode table, OPS)
function: 'TRAP' obj sourcename, obj name, 'TRAP' 8 x u32 counts (literals, parameters,
    outervalues, localvarinfos, lineinfos, defaultparams, instructions, functions),
    'TRAP' literals (obj), 'TRAP' parameters (obj), 'TRAP' outervalues (u32 type, obj src,
    obj name), 'TRAP' localvarinfos (obj name, u32 pos, start_op, end_op), 'TRAP' lineinfos
    (u32 line, u32 op), 'TRAP' defaultparams (u32), 'TRAP' instructions (i32 arg1, u8 op,
    u8 arg0, u8 arg2, u8 arg3), 'TRAP' functions, u32 stacksize, u8 bgenerator, u8 varparams
obj: u32 type, then string: u32 len + bytes / integer: i32 / float: f32 / null, bool: u32
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEAD, PART, TAIL = b"RIQS", b"TRAP", b"LIAT"
OT_NULL, OT_INTEGER, OT_FLOAT, OT_BOOL, OT_STRING = 0x01000001, 0x05000002, 0x05000004, 0x01000008, 0x08000010

# opcode numbers of the game's VM: the Squirrel 2.2 table (the file layout is 3.0's, see above)
OPS = ["LINE", "LOAD", "LOADINT", "LOADFLOAT", "DLOAD", "TAILCALL", "CALL", "PREPCALL", "PREPCALLK",
       "GETK", "MOVE", "NEWSLOT", "DELETE", "SET", "GET", "EQ", "NE", "ARITH", "BITW", "RETURN",
       "LOADNULLS", "LOADROOTTABLE", "LOADBOOL", "DMOVE", "JMP", "JNZ", "JZ", "LOADFREEVAR", "VARGC",
       "GETVARGV", "NEWTABLE", "NEWARRAY", "APPENDARRAY", "GETPARENT", "COMPARITH", "COMPARITHL", "INC",
       "INCL", "PINC", "PINCL", "CMP", "EXISTS", "INSTANCEOF", "AND", "OR", "NEG", "NOT", "BWNOT",
       "CLOSURE", "YIELD", "RESUME", "FOREACH", "POSTFOREACH", "DELEGATE", "CLONE", "TYPEOF",
       "PUSHTRAP", "POPTRAP", "THROW", "CLASS", "NEWSLOTA"]
OP = {n: i for i, n in enumerate(OPS)}


@dataclass
class Obj:
    type: int
    value: object = None

    def __repr__(self):
        return repr(self.value) if self.type != OT_NULL else "null"


def S(text: str) -> Obj:
    return Obj(OT_STRING, text)


def I(v: int) -> Obj:
    return Obj(OT_INTEGER, v)


@dataclass
class Insn:
    op: int
    arg0: int = 0
    arg1: int = 0
    arg2: int = 0
    arg3: int = 0

    def __repr__(self):
        return f"{OPS[self.op] if self.op < len(OPS) else self.op:<10} {self.arg0:3} {self.arg1:6} {self.arg2:3} {self.arg3:3}"


@dataclass
class Func:
    sourcename: Obj
    name: Obj
    literals: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    outervalues: list = field(default_factory=list)     # (type, src Obj, name Obj)
    localvarinfos: list = field(default_factory=list)   # (name Obj, pos, start_op, end_op)
    lineinfos: list = field(default_factory=list)       # (line, op)
    defaultparams: list = field(default_factory=list)
    instructions: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    stacksize: int = 0
    bgenerator: int = 0
    varparams: int = 0

    def find(self, name: str) -> "Func | None":
        if self.name.value == name:
            return self
        for f in self.functions:
            r = f.find(name)
            if r:
                return r
        return None

    def walk(self):
        yield self
        for f in self.functions:
            yield from f.walk()


class _R:
    def __init__(self, d: bytes):
        self.d, self.o = d, 0

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def tag(self, t):
        if self.d[self.o:self.o + 4] != t:
            raise ValueError(f"expected {t} at {self.o:#x}, got {self.d[self.o:self.o + 4]!r}")
        self.o += 4

    def obj(self) -> Obj:
        t = self.u32()
        if t == OT_STRING:
            n = self.u32()
            s = self.d[self.o:self.o + n].decode("utf-8")
            self.o += n
            return Obj(t, s)
        if t == OT_INTEGER:
            return Obj(t, struct.unpack("<i", struct.pack("<I", self.u32()))[0])
        if t == OT_FLOAT:
            return Obj(t, struct.unpack("<f", struct.pack("<I", self.u32()))[0])
        if t == OT_BOOL:
            return Obj(t, self.u32())
        if t == OT_NULL:
            return Obj(t)
        raise ValueError(f"object type {t:#x} at {self.o - 4:#x}")


def _func(r: _R) -> Func:
    r.tag(PART)
    f = Func(r.obj(), r.obj())
    r.tag(PART)
    nlit, npar, nout, nloc, nline, ndef, nins, nfun = (r.u32() for _ in range(8))
    r.tag(PART)
    f.literals = [r.obj() for _ in range(nlit)]
    r.tag(PART)
    f.parameters = [r.obj() for _ in range(npar)]
    r.tag(PART)
    f.outervalues = [(r.u32(), r.obj(), r.obj()) for _ in range(nout)]
    r.tag(PART)
    f.localvarinfos = [(r.obj(), r.u32(), r.u32(), r.u32()) for _ in range(nloc)]
    r.tag(PART)
    f.lineinfos = [(r.u32(), r.u32()) for _ in range(nline)]
    r.tag(PART)
    f.defaultparams = [r.u32() for _ in range(ndef)]
    r.tag(PART)
    for _ in range(nins):
        a1, op, a0, a2, a3 = struct.unpack_from("<iBBBB", r.d, r.o)
        r.o += 8
        f.instructions.append(Insn(op, a0, a1, a2, a3))
    r.tag(PART)
    f.functions = [_func(r) for _ in range(nfun)]
    f.stacksize, f.bgenerator, f.varparams = r.u32(), r.u8(), r.u8()
    return f


def parse(data: bytes) -> Func:
    r = _R(data)
    if data[:2] != b"\xfa\xfa":
        raise ValueError("not a cnut")
    r.o = 2
    r.tag(HEAD)
    if r.u32() != 1:
        raise ValueError("SQChar size != 1")
    f = _func(r)
    r.tag(TAIL)
    if r.o != len(data):
        raise ValueError(f"trailing data at {r.o:#x}")
    return f


def _obj(o: Obj) -> bytes:
    b = struct.pack("<I", o.type)
    if o.type == OT_STRING:
        s = o.value.encode("utf-8")
        return b + struct.pack("<I", len(s)) + s
    if o.type == OT_INTEGER:
        return b + struct.pack("<i", o.value)
    if o.type == OT_FLOAT:
        return b + struct.pack("<f", o.value)
    if o.type == OT_BOOL:
        return b + struct.pack("<I", o.value)
    return b


def _build(f: Func) -> bytes:
    out = [PART, _obj(f.sourcename), _obj(f.name), PART,
           struct.pack("<8I", len(f.literals), len(f.parameters), len(f.outervalues), len(f.localvarinfos),
                       len(f.lineinfos), len(f.defaultparams), len(f.instructions), len(f.functions)), PART]
    out += [_obj(o) for o in f.literals]
    out.append(PART)
    out += [_obj(o) for o in f.parameters]
    out.append(PART)
    for t, s, n in f.outervalues:
        out += [struct.pack("<I", t), _obj(s), _obj(n)]
    out.append(PART)
    for n, p, a, b in f.localvarinfos:
        out += [_obj(n), struct.pack("<3I", p, a, b)]
    out.append(PART)
    out += [struct.pack("<2I", *li) for li in f.lineinfos]
    out.append(PART)
    out += [struct.pack("<I", d) for d in f.defaultparams]
    out.append(PART)
    out += [struct.pack("<iBBBB", i.arg1, i.op, i.arg0, i.arg2, i.arg3) for i in f.instructions]
    out.append(PART)
    out += [_build(g) for g in f.functions]
    out.append(struct.pack("<IBB", f.stacksize, f.bgenerator, f.varparams))
    return b"".join(out)


def build(f: Func) -> bytes:
    return b"\xfa\xfa" + HEAD + struct.pack("<I", 1) + _build(f) + TAIL


def disasm(f: Func, indent: str = "") -> str:
    """Readable listing: literals, locals and instructions with literal names resolved."""
    out = [f"{indent}function {f.name!r} params={f.parameters} stack={f.stacksize} varparams={f.varparams}"]
    for k, o in enumerate(f.literals):
        out.append(f"{indent}  lit {k:3} {o!r}")
    for n, p, a, b in f.localvarinfos:
        out.append(f"{indent}  local {n!r} slot {p} ops {a}..{b}")
    for k, i in enumerate(f.instructions):
        note = ""
        if i.op in (OP["LOAD"], OP["PREPCALLK"], OP["GETK"]) and 0 <= i.arg1 < len(f.literals):
            note = f"  ; {f.literals[i.arg1]!r}"
        elif i.op == OP["DLOAD"] and i.arg3 < len(f.literals):
            note = f"  ; {f.literals[i.arg1]!r} {f.literals[i.arg3]!r}"
        elif i.op == OP["CLOSURE"]:
            note = f"  ; {f.functions[i.arg1].name!r}"
        out.append(f"{indent}  {k:4} {i!r}{note}")
    for g in f.functions:
        out.append(disasm(g, indent + "    "))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    from .pack import Pack
    data = open(sys.argv[1], "rb").read()
    if data[:2] != b"\xfa\xfa":
        data = Pack.parse(data).entries[0].data
    f = parse(data)
    assert build(f) == data, "round trip differs"
    sys.stdout.reconfigure(encoding="utf-8")
    print(disasm(f) if "--only" not in sys.argv else disasm(f.find(sys.argv[sys.argv.index("--only") + 1])))
