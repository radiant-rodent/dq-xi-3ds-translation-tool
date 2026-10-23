"""Test builds only: the "test room" - talking to anyone in an area runs a test event.

The area script (romfs/script/<area>.pack, compiled Squirrel, see sqbin.py) defines a table of
event functions; the game calls `<area>.TrgRCommand(type)` when the hero interacts (type 10 =
talk to a character) and the original function starts the area's scripted talks. The test
build renames the original to TrgRCommand_orig and adds:

    function TrgRCommand(type) {
        if (type == 10) { StartEvent(this.IE_TEST); return; }
        TrgRCommand_orig(type);
    }
    function IE_TEST() { <steps> }

`steps` is a list of native calls with integer / string / bool arguments, e.g.
    [("LoadScenarioBattleEncount", 25)]              battle (scenario_battle.bxon entry)
    [("StartUI", 24, "@target"), ("WaitMenu",), ("EndUI", 24)]   church menu
    ("@target" = GetTargetActorID(), "this.X" = a member of the area table, a tuple = the
    result of a nested native call, e.g. ("GetPartyMemberMaxHP", ("GetPlayerID", 0)))
    mode=0 / 1: 2D / 3D (GetDisplayMode; 2 = 3D top + 2D bottom). In the other mode the first
    talk only switches the mode (common.SwitchDispMode reloads the field, the hero stays at
    the priest) and the second talk runs the steps.
The save `user_citra` resumes in front of the Cobblestone priest, so one A press starts it.
"""
from __future__ import annotations

from . import sqbin
from .sqbin import OP, Func, Insn, Obj, I, S

AREA_SCRIPT = "/script/b011.pack"      # Cobblestone (save user_citra: church, facing the priest)
TALK = 10                              # TrgRCommand type: talk to a character


class Asm:
    """Builds one function's instructions + literal table."""
    def __init__(self, name: str, params: list[str], source: Obj):
        self.f = Func(source, S(name), parameters=[S(p) for p in params])
        self.nreg = len(params)

    def lit(self, v) -> int:
        o = S(v) if isinstance(v, str) else I(v)
        for k, x in enumerate(self.f.literals):
            if x.type == o.type and x.value == o.value:
                return k
        self.f.literals.append(o)
        return len(self.f.literals) - 1

    def emit(self, op: str, a0=0, a1=0, a2=0, a3=0) -> int:
        self.f.instructions.append(Insn(OP[op], a0, a1, a2, a3))
        return len(self.f.instructions) - 1

    def load_arg(self, reg: int, v):
        if isinstance(v, tuple):                        # nested native call: its result
            self.call(v[0], *v[1:], base=reg)
        elif v == "@target":                              # the character talked to
            self.emit("PREPCALLK", reg, self.lit("GetTargetActorID"), 0, reg + 1)
            self.emit("CALL", reg, reg, reg + 1, 1)
        elif v == "@priest":                            # the church's priest (as ChurchAfterLoad)
            self.emit("PREPCALLK", reg, self.lit("FindActor"), 0, reg + 1)
            self.emit("LOADINT", reg + 2, 0)
            self.emit("CALL", reg, reg, reg + 1, 2)
        elif isinstance(v, bool):
            self.emit("LOADBOOL", reg, int(v))
        elif isinstance(v, int):
            self.emit("LOADINT", reg, v)
        elif isinstance(v, str) and v.startswith("this."):
            self.emit("GETK", reg, self.lit(v[5:]), 0)
        else:
            self.emit("LOAD", reg, self.lit(v))

    def call(self, name: str, *args, base: int | None = None):
        """name(args...) as a statement: closure in base, `this` + args above it."""
        r = self.nreg if base is None else base
        self.emit("PREPCALLK", r, self.lit(name), 0, r + 1)
        for k, a in enumerate(args):
            self.load_arg(r + 2 + k, a)
        self.emit("CALL", r, r, r + 1, len(args) + 1)
        self.f.stacksize = max(self.f.stacksize, r + 3 + len(args))

    def done(self) -> Func:
        self.emit("RETURN", 255)
        f = self.f
        n = len(f.instructions)
        f.localvarinfos = [(p, k, 0, n - 1) for k, p in reversed(list(enumerate(f.parameters)))]
        f.lineinfos = [(1, 0)]
        f.stacksize = max(f.stacksize, self.nreg + 2)
        return f


def _trg(source: Obj) -> Func:
    a = Asm("TrgRCommand", ["this", "type"], source)
    a.emit("LOADINT", 2, TALK)
    a.emit("EQ", 2, 2, 1, 0)                         # r2 = (type == 10)
    jz = a.emit("JZ", 2, 0)
    a.call("StartEvent", "this.IE_TEST", base=3)
    a.emit("RETURN", 255)
    a.f.instructions[jz].arg1 = len(a.f.instructions) - jz - 1
    a.emit("PREPCALLK", 2, a.lit("TrgRCommand_orig"), 0, 3)
    a.emit("MOVE", 4, 1)
    a.emit("CALL", 2, 2, 3, 2)
    a.f.stacksize = 8
    return a.done()


def _event(steps: list, source: Obj, mode: int | None = None) -> Func:
    """steps as native calls. mode (0 = 2D, 1 = 3D): if the game is in the other mode, this
    talk only switches (common.SwitchDispMode: the field reloads in front of the priest) and
    the next talk runs the steps."""
    a = Asm("IE_TEST", ["this"], source)
    jz = None
    if mode is not None:
        a.call("GetDisplayMode", base=1)                # r1 = GetDisplayMode()
        a.emit("LOADINT", 2, mode)
        a.emit("EQ", 2, 2, 1, 0)
        jz = a.emit("JZ", 2, 0)
    for st in steps:
        a.call(st[0], *st[1:])
    if jz is not None:
        a.emit("RETURN", 255)
        a.f.instructions[jz].arg1 = len(a.f.instructions) - jz - 1
        a.emit("GETK", 1, a.lit("common"), 0)           # r1 = this.common (root via delegate)
        a.emit("PREPCALLK", 2, a.lit("SwitchDispMode"), 1, 3)
        a.emit("CALL", 2, 2, 3, 1)
    return a.done()


def inject(data: bytes, steps: list, mode: int | None = None) -> bytes:
    """The area script (cnut) with the test TrgRCommand + IE_TEST."""
    main = sqbin.parse(data)
    names = [f.name.value for f in main.functions]
    if "TrgRCommand" not in names or "StartEvent" not in names:
        raise ValueError("area script without TrgRCommand / StartEvent")
    ins = main.instructions
    # the slot key of the original TrgRCommand becomes TrgRCommand_orig
    key = next(k for k, o in enumerate(main.literals) if o.value == "TrgRCommand")
    main.literals[key] = S("TrgRCommand_orig")
    main.find("TrgRCommand").name = S("TrgRCommand_orig")
    newslot = next(k for k, i in enumerate(ins) if i.op == OP["NEWSLOT"])
    src = main.functions[0].sourcename
    add = []
    for fn in (_trg(src), _event(steps, src, mode)):
        main.functions.append(fn)
        main.literals.append(S(fn.name.value))
        add += [Insn(OP["LOAD"], 3, len(main.literals) - 1), Insn(OP["CLOSURE"], 4, len(main.functions) - 1),
                Insn(OP["NEWSLOTA"], 0, 2, 3, 4)]       # table r2 [r3] = r4, like the original slots
    main.instructions = ins[:newslot] + add + ins[newslot:]
    n = len(add)
    main.localvarinfos = [(nm, p, a, b + n if b >= newslot else b) for nm, p, a, b in main.localvarinfos]
    main.lineinfos = [(ln, op + n if op >= newslot else op) for ln, op in main.lineinfos]
    next(i for i in ins if i.op == OP["NEWTABLE"]).arg1 += 2     # size hint
    return sqbin.build(main)


def patch_pack(pack_bytes: bytes, steps: list, mode: int | None = None) -> bytes:
    from .pack import Pack
    p = Pack.parse(pack_bytes)
    for e in p.entries:
        if e.data[:2] == b"\xfa\xfa":
            e.data = inject(e.data, steps, mode)
            return p.build()
    raise ValueError("no script in pack")
