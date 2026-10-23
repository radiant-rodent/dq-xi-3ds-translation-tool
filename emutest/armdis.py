"""dis.py ADDR [N] - disassemble the JP .code (ARM) at a virtual address"""
import sys, capstone
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import az
c = open(sys.argv[3] if len(sys.argv) > 3 else os.path.join(az.ROOT, 'code_jp.bin'), 'rb').read()
a = int(sys.argv[1], 16); n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
for i in md.disasm(c[a - 0x100000:a - 0x100000 + 4 * n], a):
    print(f"{i.address:08x}  {i.mnemonic:8s} {i.op_str}")
