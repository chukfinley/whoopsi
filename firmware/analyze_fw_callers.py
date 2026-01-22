#!/usr/bin/env python3
"""Examine callers of CRC16 (0x0127FC) and CRC32 (0x012804) functions.
Focus on firmware update validation flow."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

def disasm(start, length=2048):
    return list(md.disasm(data[start:start+length], start))

def find_func_start(addr):
    for off in range(2, 8192, 2):
        p = addr - off
        if p < 0: return None
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
            return p
    return None

# CRC16 callers (init=0xFFFF, calls 0x0127FC)
CRC16_CALLERS = [0x012926, 0x012B2A, 0x012BF8, 0x0301B4, 0x0345BE, 0x034B48, 
                  0x049558, 0x049630, 0x04969C]

# CRC32 callers (0x012804)
CRC32_CALLERS = [0x01274C, 0x01277C]

# CRC verify callers (0x0127A0 - let's check what this does)
print("=== FUNCTION AT 0x0127A0 ===")
instrs = disasm(0x0127A0, 128)
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}")
    if ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
        break

# All CRC16 callers - show the caller's function
print("\n" + "=" * 70)
print("CRC16 CALLER FUNCTIONS")
print("=" * 70)

seen_funcs = set()
for caller in CRC16_CALLERS:
    fs = find_func_start(caller)
    if fs and fs in seen_funcs:
        continue
    if fs:
        seen_funcs.add(fs)
    else:
        fs = max(0, caller - 64)
    
    print(f"\n--- Function at 0x{fs:06X} (CRC16 call at 0x{caller:06X}) ---")
    func_instrs = disasm(fs, min(2048, 1024))
    
    # Show full function up to end, but max 150 instructions
    count = 0
    for ins in func_instrs:
        mark = " <<<CRC16" if ins.address == caller else ""
        # Also mark BL calls
        if ins.mnemonic == "bl":
            try:
                target = int(ins.op_str.replace("#",""), 0)
                if target == 0x0127A0: mark += " [CRC16_verify]"
                elif target == 0x012804: mark += " [CRC32]"
                elif target == 0x0127FC: mark += " [CRC16_init]"
            except: pass
        print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}{mark}")
        count += 1
        if count > 5 and ins.mnemonic in ("pop","pop.w") and "pc" in ins.op_str:
            break
        if count > 150:
            print("  ... (truncated)")
            break

# Now let's find callers of these CRC16-using functions
# Focus on 0x0301B4's function, 0x0345BE's function, 0x049558's function
print("\n\n" + "=" * 70)
print("CALLERS OF CRC-USING FUNCTIONS (2nd level)")
print("=" * 70)

for caller in CRC16_CALLERS:
    fs = find_func_start(caller)
    if not fs: continue
    
    # Find all BL to this function
    target_addr = fs
    found = False
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
            if S: imm32 |= 0xFE000000
            target = (off + 4 + imm32) & 0xFFFFFFFF
            if target == target_addr or target == target_addr + 1:
                if not found:
                    print(f"\n  Callers of 0x{fs:06X}:")
                    found = True
                print(f"    BL at 0x{off:06X}")
    
    if not found:
        # Maybe it's called via function pointer
        fptr = fs | 1  # Thumb
        target = struct.pack("<I", fptr)
        pos = data.find(target)
        if pos != -1:
            print(f"\n  Function pointer to 0x{fs:06X} at 0x{pos:06X}")

