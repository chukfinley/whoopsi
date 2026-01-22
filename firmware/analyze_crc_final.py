#!/usr/bin/env python3
"""The CRC16 function loads its table from 0x0F53B4. Let's verify.
Also: function at 0x0127FC wraps with init=0xFFFF and falls through to 0x127D8.
Function at 0x012804 is a related (possibly CRC32) function."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# Verify CRC16 table at 0x0F53B4
print("=== CRC TABLE AT 0x0F53B4 ===")
for i in range(8):
    val = struct.unpack_from("<H", data, 0x0F53B4 + i*2)[0]
    print(f"  [{i}] 0x{val:04X}")

# Check the KNOWN CRC16 table at 0x0AC3B4
print("\n=== CRC TABLE AT 0x0AC3B4 ===")
for i in range(8):
    val = struct.unpack_from("<H", data, 0x0AC3B4 + i*2)[0]
    print(f"  [{i}] 0x{val:04X}")

# Function at 0x012804 (right after CRC16)
print("\n=== FUNCTION AT 0x012804 ===")
instrs = list(md.disasm(data[0x012804:0x012804+256], 0x012804))
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}")
    if ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
        break

# Now search for ALL BL targets that land in the 0x0127D8-0x012900 range
print("\n=== ALL BL CALLS INTO 0x012700-0x012900 RANGE ===")
for off in range(0, len(data) - 3, 2):
    hw1 = struct.unpack_from("<H", data, off)[0]
    hw2 = struct.unpack_from("<H", data, off+2)[0]
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
            imm32 |= 0xFE000000
        target = (off + 4 + imm32) & 0xFFFFFFFF
        if 0x012700 <= target <= 0x012900:
            print(f"  BL at 0x{off:06X} -> 0x{target:06X}")

# Also search for function pointers to anything in 0x0127xx-0x0128xx
print("\n=== FUNCTION POINTERS INTO CRC REGION ===")
for target_addr in [0x0127D9, 0x0127FD, 0x012805]:
    target = struct.pack("<I", target_addr)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  0x{target_addr:08X} at offset 0x{pos:06X}")
        pos += 1

# The entry point 0x0127FC: movw r2, #0xffff; b 0x127d8
# This is the CRC16_INIT function. Search for calls to it:
print("\n=== BL TO 0x0127FC ===")
for off in range(0, len(data) - 3, 2):
    hw1 = struct.unpack_from("<H", data, off)[0]
    hw2 = struct.unpack_from("<H", data, off+2)[0]
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
            imm32 |= 0xFE000000
        target = (off + 4 + imm32) & 0xFFFFFFFF
        if target == 0x0127FC or target == 0x0127FD:
            print(f"  BL at 0x{off:06X} -> 0x{target:06X}")

