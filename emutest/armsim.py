"""armsim.py - a tiny ARM (A32) interpreter for testing hand-written code-cave routines
offline (no emulator): data processing, ldr/str (b/h, offsets, pre/post-index), push/pop,
ldm/stm, b/bl/bx, conditions. Enough for dq11tool's patches; raises on anything else.

    sim = Sim(code, base); sim.mem[addr] = ...; sim.call(entry, r0, r1)
"""
from __future__ import annotations

import capstone
from capstone import arm as A

M32 = 0xFFFFFFFF


class Sim:
    def __init__(self, code: bytes, base: int, mem_size: int = 0x10000, mem_base: int = 0x10000000):
        self.md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
        self.md.detail = True
        self.code, self.base = code, base
        self.mem = bytearray(mem_size)
        self.mem_base = mem_base
        self.r = [0] * 16
        self.n = self.z = self.c = self.v = False
        self.steps = 0
        self._cache = {}

    # ---- memory (data area and the code itself, read-only)
    def _loc(self, a: int, size: int):
        if self.mem_base <= a and a + size <= self.mem_base + len(self.mem):
            return self.mem, a - self.mem_base
        if self.base <= a and a + size <= self.base + len(self.code):
            return self.code, a - self.base
        raise IndexError(f"bad address {a:08x} (pc {self.r[15]:08x})")

    def rd(self, a, size):
        buf, o = self._loc(a, size)
        return int.from_bytes(buf[o:o + size], "little")

    def wr(self, a, size, v):
        buf, o = self._loc(a, size)
        if buf is self.code:
            raise IndexError(f"write to code {a:08x}")
        buf[o:o + size] = (v & ((1 << (8 * size)) - 1)).to_bytes(size, "little")

    # ---- helpers
    def cond(self, cc):
        n, z, c, v = self.n, self.z, self.c, self.v
        return {A.ARM_CC_INVALID: True, A.ARM_CC_AL: True, A.ARM_CC_EQ: z, A.ARM_CC_NE: not z,
                A.ARM_CC_HS: c, A.ARM_CC_LO: not c, A.ARM_CC_MI: n, A.ARM_CC_PL: not n,
                A.ARM_CC_VS: v, A.ARM_CC_VC: not v, A.ARM_CC_HI: c and not z, A.ARM_CC_LS: (not c) or z,
                A.ARM_CC_GE: n == v, A.ARM_CC_LT: n != v, A.ARM_CC_GT: (not z) and n == v,
                A.ARM_CC_LE: z or n != v}[cc]

    def reg(self, r):
        i = self._ri(r)
        return (self.r[15] + 8) & M32 if i == 15 else self.r[i]

    def _ri(self, r):
        names = {A.ARM_REG_R0 + i: i for i in range(13)}
        names.update({A.ARM_REG_SB: 9, A.ARM_REG_SL: 10, A.ARM_REG_FP: 11, A.ARM_REG_IP: 12,
                      A.ARM_REG_SP: 13, A.ARM_REG_LR: 14, A.ARM_REG_PC: 15})
        return names[r]

    def shifted(self, op):
        v = self.reg(op.reg) if op.type == A.ARM_OP_REG else op.imm & M32
        st, sv = op.shift.type, op.shift.value
        if st == A.ARM_SFT_INVALID or sv == 0 and st in (A.ARM_SFT_LSL,):
            return v
        if st in (A.ARM_SFT_LSL_REG, A.ARM_SFT_LSR_REG, A.ARM_SFT_ASR_REG, A.ARM_SFT_ROR_REG):
            sv = self.r[self._ri(sv)] & 0xFF
            st = {A.ARM_SFT_LSL_REG: A.ARM_SFT_LSL, A.ARM_SFT_LSR_REG: A.ARM_SFT_LSR,
                  A.ARM_SFT_ASR_REG: A.ARM_SFT_ASR, A.ARM_SFT_ROR_REG: A.ARM_SFT_ROR}[st]
        if st == A.ARM_SFT_LSL:
            return (v << sv) & M32
        if st == A.ARM_SFT_LSR:
            return v >> sv
        if st == A.ARM_SFT_ASR:
            return ((v - (1 << 32) if v >> 31 else v) >> sv) & M32
        if st == A.ARM_SFT_ROR:
            return ((v >> sv) | (v << (32 - sv))) & M32
        raise NotImplementedError(st)

    def flags_add(self, a, b, carry=0):
        res = a + b + carry
        r = res & M32
        self.n, self.z, self.c = bool(r >> 31), r == 0, res > M32
        self.v = bool((~(a ^ b) & (a ^ r)) >> 31 & 1)
        return r

    def flags_sub(self, a, b):
        r = (a - b) & M32
        self.n, self.z, self.c = bool(r >> 31), r == 0, a >= b
        self.v = bool(((a ^ b) & (a ^ r)) >> 31 & 1)
        return r

    def addr(self, op, insn):
        m = op.mem
        base = self.reg(m.base)
        off = 0
        if m.index:
            off = self.r[self._ri(m.index)]
            if op.shift.type != A.ARM_SFT_INVALID:
                off = (off << op.shift.value) & M32
            if op.subtracted:
                off = -off
        else:
            off = m.disp
        return base, off

    # ---- run
    def insn(self, pc):
        if pc not in self._cache:
            o = pc - self.base
            self._cache[pc] = next(self.md.disasm(self.code[o:o + 4], pc))
        return self._cache[pc]

    def call(self, entry, *args, sp=None, max_steps=2_000_000):
        for i, a in enumerate(args):
            self.r[i] = a & M32
        self.r[13] = sp if sp is not None else self.mem_base + len(self.mem) - 0x10
        self.r[14] = 0xDEAD0000
        self.r[15] = entry
        start = self.steps
        while self.r[15] != 0xDEAD0000:
            self.step()
            self.steps += 1
            if self.steps - start > max_steps:
                raise RuntimeError("too many steps")
        return self.r[0]

    def step(self):
        pc = self.r[15]
        i = self.insn(pc)
        ops = i.operands
        mn = i.mnemonic
        base_mn = mn[:-2] if i.cc != A.ARM_CC_AL and mn[-2:] in ("eq", "ne", "hs", "lo", "mi", "pl", "vs", "vc",
                                                                  "hi", "ls", "ge", "lt", "gt", "le", "cs", "cc") else mn
        nxt = pc + 4
        if not self.cond(i.cc):
            self.r[15] = nxt
            return
        S = i.update_flags
        if base_mn.endswith("s") and base_mn[:-1] in ("mov", "add", "sub", "orr", "and", "lsl", "lsr", "mul", "rsb"):
            base_mn = base_mn[:-1]

        def setd(idx, v):
            nonlocal nxt
            if idx == 15:
                nxt = v & ~1
            else:
                self.r[idx] = v & M32

        if base_mn in ("mov", "mvn"):
            v = self.shifted(ops[1])
            if base_mn == "mvn":
                v = ~v & M32
            if S:
                self.n, self.z = bool(v >> 31), v == 0
            setd(self._ri(ops[0].reg), v)
        elif base_mn in ("add", "sub", "rsb", "orr", "and", "eor", "bic", "mul", "lsl", "lsr"):
            if base_mn in ("lsl", "lsr") and len(ops) == 2:     # capstone: shift kept on the operand
                v = self.shifted(ops[1])
                if S:
                    self.n, self.z = bool(v >> 31), v == 0
            elif base_mn in ("lsl", "lsr"):
                a = self.reg(ops[1].reg)
                sh = self.shifted(ops[2]) & 0xFF
                v = (a << sh) & M32 if base_mn == "lsl" else a >> sh
                if S:
                    self.n, self.z = bool(v >> 31), v == 0
            else:
                a = self.reg(ops[1].reg)
                b = self.shifted(ops[2]) if len(ops) > 2 else self.reg(ops[1].reg)
                if len(ops) == 2:
                    a, b = self.reg(ops[0].reg), self.shifted(ops[1])
                if base_mn == "add":
                    v = self.flags_add(a, b) if S else (a + b) & M32
                elif base_mn == "sub":
                    v = self.flags_sub(a, b) if S else (a - b) & M32
                elif base_mn == "rsb":
                    v = self.flags_sub(b, a) if S else (b - a) & M32
                elif base_mn == "mul":
                    v = (a * b) & M32
                    if S:
                        self.n, self.z = bool(v >> 31), v == 0
                else:
                    v = {"orr": a | b, "and": a & b, "eor": a ^ b, "bic": a & ~b}[base_mn] & M32
                    if S:
                        self.n, self.z = bool(v >> 31), v == 0
            setd(self._ri(ops[0].reg), v)
        elif base_mn in ("cmp", "cmn", "tst", "teq"):
            a, b = self.reg(ops[0].reg), self.shifted(ops[1])
            if base_mn == "cmp":
                self.flags_sub(a, b)
            elif base_mn == "cmn":
                self.flags_add(a, b)
            else:
                v = a & b if base_mn == "tst" else a ^ b
                self.n, self.z = bool(v >> 31), v == 0
        elif base_mn in ("uxth", "uxtb"):
            v = self.reg(ops[1].reg) & (0xFFFF if base_mn == "uxth" else 0xFF)
            setd(self._ri(ops[0].reg), v)
        elif base_mn in ("ldr", "ldrh", "ldrb", "str", "strh", "strb", "ldrsh", "ldrsb"):
            size = {"ldr": 4, "str": 4, "ldrh": 2, "strh": 2, "ldrb": 1, "strb": 1, "ldrsh": 2, "ldrsb": 1}[base_mn]
            mop = ops[1]
            base, off = self.addr(mop, i)
            post = len(ops) > 2
            if post:
                off2 = self.shifted(ops[2]) if ops[2].type != A.ARM_OP_IMM else ops[2].imm
                if ops[2].subtracted:
                    off2 = -off2
                a = base
            else:
                a = (base + off) & M32
            if base_mn.startswith("ld"):
                v = self.rd(a, size)
                if base_mn == "ldrsh" and v & 0x8000:
                    v -= 0x10000
                if base_mn == "ldrsb" and v & 0x80:
                    v -= 0x100
                setd(self._ri(ops[0].reg), v & M32)
            else:
                self.wr(a, size, self.reg(ops[0].reg))
            if post:
                self.r[self._ri(mop.mem.base)] = (base + off2) & M32
            elif i.writeback:
                self.r[self._ri(mop.mem.base)] = a
        elif base_mn in ("push", "pop", "ldm", "stm", "stmdb", "ldmia", "stmia", "ldmfd", "stmfd"):
            if base_mn in ("push", "pop"):
                regs = [self._ri(o.reg) for o in ops]
                sp = self.r[13]
                if base_mn == "push":
                    sp -= 4 * len(regs)
                    for k, rr in enumerate(sorted(regs)):
                        self.wr(sp + 4 * k, 4, self.r[rr] if rr != 15 else pc + 8)
                    self.r[13] = sp
                else:
                    for k, rr in enumerate(sorted(regs)):
                        setd(rr, self.rd(sp + 4 * k, 4))
                    self.r[13] = sp + 4 * len(regs)
            else:
                bi = self._ri(ops[0].reg)
                regs = [self._ri(o.reg) for o in ops[1:]]
                a = self.r[bi]
                if base_mn == "stmdb":
                    a -= 4 * len(regs)
                    for k, rr in enumerate(sorted(regs)):
                        self.wr(a + 4 * k, 4, self.r[rr])
                    if i.writeback:
                        self.r[bi] = a
                elif base_mn.startswith("ld"):
                    for k, rr in enumerate(sorted(regs)):
                        setd(rr, self.rd(a + 4 * k, 4))
                    if i.writeback:
                        self.r[bi] = a + 4 * len(regs)
                else:
                    for k, rr in enumerate(sorted(regs)):
                        self.wr(a + 4 * k, 4, self.r[rr])
                    if i.writeback:
                        self.r[bi] = a + 4 * len(regs)
        elif base_mn in ("b", "bl"):
            if base_mn == "bl":
                self.r[14] = pc + 4
            nxt = ops[0].imm
        elif base_mn in ("bx", "blx"):
            t = self.reg(ops[0].reg) if ops[0].type == A.ARM_OP_REG else ops[0].imm
            if base_mn == "blx":
                self.r[14] = pc + 4
            nxt = t & ~1
        elif base_mn == "adr":
            setd(self._ri(ops[0].reg), (pc + 8 + ops[1].imm) & M32)
        elif base_mn == "nop":
            pass
        else:
            raise NotImplementedError(f"{pc:08x}: {mn} {i.op_str}")
        self.r[15] = nxt
