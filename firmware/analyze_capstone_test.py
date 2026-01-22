import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# Check the LDR at 0x183C4 which we can see in the disassembly
# 0x183C4: ldr r3, [pc, #0x19c]
instrs = list(md.disasm(data[0x183C4:0x183C4+4], 0x183C4))
if instrs:
    ins = instrs[0]
    print(f"Instruction: {ins.mnemonic} {ins.op_str}")
    print(f"  Operand count: {len(ins.operands)}")
    for i, op in enumerate(ins.operands):
        print(f"  Op[{i}] type={op.type} ", end="")
        if op.type == 1: print(f"REG={op.reg}")
        elif op.type == 2: print(f"IMM={op.imm}")
        elif op.type == 4: print(f"MEM base={op.mem.base} index={op.mem.index} disp={op.mem.disp}")
        else: print()
    
    # Compute literal pool address manually
    pool_addr = ((ins.address + 4) & ~3) + 0x19c
    val = struct.unpack_from("<I", data, pool_addr)[0]
    print(f"\n  Literal pool at 0x{pool_addr:06X} = 0x{val:08X}")
    
    # What is this value? Is it a function pointer, data address?
    if val < len(data):
        print(f"  At that address: {data[val:val+16].hex()}")

# Now let me also check: is the code perhaps using TBB/TBH (table branch) for command dispatch?
print("\n\nSearching for TBB/TBH instructions in first 640KB...")
for chunk_start in range(0, 0xA0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("tbb", "tbh"):
            print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# Also check for LDR to a jump table (LDR PC, [PC, Rm, LSL #2])
print("\nSearching for computed jumps (LDR PC, ...)...")
for chunk_start in range(0, 0xA0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("ldr", "ldr.w") and "pc" in ins.op_str.lower() and ins.operands[0].reg == 15:
            print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

