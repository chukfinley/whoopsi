#\!/usr/bin/env python3
"""Get full CRC16 function, search for callers more carefully."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# Full function - don't stop at first POP
print("=== FULL CRC16 FUNCTION at 0x0127D8 ===")
instrs = list(md.disasm(data[0x0127D8:0x0127D8+64], 0x0127D8))
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}")

# The function loads r4 from [pc+0x1c]. At 0x0127DA, PC+4=0x0127DE, aligned=0x0127DC
# Pool addr = 0x0127DC + 0x1C = 0x0127F8
pool_val = struct.unpack_from("<I", data, 0x0127F8)[0]
print(f"\n  Literal pool at 0x0127F8 = 0x{pool_val:08X}")
# Verify this is the CRC16 table address
if pool_val == 0x0AC3B4:
    print("  -> Confirmed: CRC16 table address\!")

# The BL to this function: Thumb BL is encoded as two halfwords
# F000 F8XX for forward short BL, or Fxxx Fxxx for full range
# Let's search by computing the BL encoding for common caller distances

# Actually, let me just search for ALL BL instructions in the binary
# and check which ones target 0x0127D8 or 0x0127D9
print("\n=== SEARCHING FOR BL TO 0x0127D8/0x0127D9 ===")
# Thumb BL: 11110 Simm10H : 11J1 Jimm11L
# The target = PC + SignExtend(S:I1:I2:imm10:imm11:0)
# where I1 = NOT(J1 XOR S), I2 = NOT(J2 XOR S)

found_calls = []
for off in range(0, len(data) - 3, 2):
    hw1 = struct.unpack_from("<H", data, off)[0]
    hw2 = struct.unpack_from("<H", data, off+2)[0]
    
    # Check if this is a BL instruction
    # hw1: 1111 0Sxx xxxx xxxx  (top 5 bits = 11110)
    # hw2: 11x1 xxxx xxxx xxxx  (top 2 bits = 11, bit 12 = 1 for BL)
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
        S = (hw1 >> 10) & 1
        imm10 = hw1 & 0x3FF
        J1 = (hw2 >> 13) & 1
        J2 = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        I1 = 1 - (J1 ^ S)
        I2 = 1 - (J2 ^ S)
        
        imm32 = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
        if S:
            imm32 |= 0xFE000000  # Sign extend
        
        target = (off + 4 + imm32) & 0xFFFFFFFF
        if target == 0x0127D8 or target == 0x0127D9:
            found_calls.append(off)

print(f"Found {len(found_calls)} BL instructions targeting CRC16 function:")
for addr in found_calls:
    print(f"  BL at 0x{addr:06X}")

# If still zero, the function might be called via function pointer
# Let's search for 0x0127D9 (Thumb address) as a value in data
print("\nSearching for function pointer 0x0127D9...")
target = struct.pack("<I", 0x0127D9)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"  Found at 0x{pos:06X}")
    pos += 1

# Also check if there's a DIFFERENT CRC function. Let's search more broadly.
print("\n=== ALL LDRH WITH LSL #1 (potential CRC lookups) ===")
for chunk_start in range(0, len(data), 0x10000):
    chunk = data[chunk_start:min(chunk_start+0x10000, len(data))]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("ldrh", "ldrh.w") and "lsl #1" in ins.op_str:
            print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

