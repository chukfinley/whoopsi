#!/usr/bin/env python3
"""
KEY INSIGHT: The strings in 0x0A-0x0C range are LOG STRINGS that are not 
directly referenced by code pointers. They're likely stored as a separate 
section for a logging framework.

The code at 0x0E0000+ region has high entropy and contains the actual 
firmware logic. The pointer table at 0x0A8760 points into 0x0EE000+ which 
is encoded data.

Strategy: 
1. Find the code that handles external flash writes (for firmware update)
2. Look for the CRC16 computation function
3. Find the BLE command handler by looking for the actual dispatch mechanism

Since we can't trace from strings, let's find the CRC function by its 
algorithm pattern: XOR, shift, table lookup.
"""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# The CRC16 lookup table at 0x0AC3B4. Let's verify it IS a CRC16 table.
print("=== CRC16 TABLE VERIFICATION ===")
# CRC-CCITT table starts with 0x0000, 0x1021, 0x2042, 0x3063, ...
# or reflected: 0x0000, 0xC0C1, 0xC181, 0x0140, ...
entries = []
for i in range(8):
    val = struct.unpack_from("<H", data, 0x0AC3B4 + i*2)[0]
    entries.append(val)
    print(f"  [{i}] 0x{val:04X}")

if entries[1] == 0x1021:
    print("  -> CRC-CCITT (normal) table")
elif entries[1] == 0xC0C1:
    print("  -> CRC-16 (reflected) table") 
else:
    print(f"  -> Unknown CRC variant")

# CRC16 table is 256 entries * 2 bytes = 512 bytes
# Table spans 0x0AC3B4 to 0x0AC5B4
# Code that uses this table would:
# 1. Load table base address
# 2. XOR input byte with current CRC low byte
# 3. Use result as index into table
# 4. XOR with shifted CRC

# Since we couldn't find direct references, let's search for the pattern:
# LDRH Rt, [Rn, Rm, LSL #1] - table lookup with index shifted by 1
print("\n=== SEARCHING FOR CRC COMPUTATION PATTERNS ===")
print("Looking for LDRH with LSL #1 (table lookup)...")

for chunk_start in range(0, 0x0A0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("ldrh", "ldrh.w"):
            if "lsl #1" in ins.op_str:
                # Found a table lookup - check surrounding code for CRC pattern
                # Look for XOR (EOR) nearby
                start = max(0, ins.address - 32)
                context = list(md.disasm(data[start:start+96], start))
                has_eor = any(i.mnemonic in ("eor", "eor.w", "eors", "eors.w") 
                             for i in context)
                has_lsr = any(i.mnemonic in ("lsr", "lsr.w", "lsrs", "lsrs.w") 
                             and "#8" in i.op_str for i in context)
                has_uxtb = any(i.mnemonic == "uxtb" for i in context)
                
                if has_eor and (has_lsr or has_uxtb):
                    print(f"\n  PROBABLE CRC FUNCTION at 0x{ins.address:06X}:")
                    # Find function start
                    fs = None
                    for off in range(2, 4096, 2):
                        p = ins.address - off
                        if p < 0: break
                        hw = struct.unpack_from("<H", data, p)[0]
                        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
                            fs = p
                            break
                    if fs:
                        func_instrs = list(md.disasm(data[fs:fs+256], fs))
                        for fi in func_instrs:
                            mark = " <<<" if fi.address == ins.address else ""
                            print(f"    0x{fi.address:06X}: {fi.mnemonic:10s} {fi.op_str}{mark}")
                            if fi.mnemonic in ("pop", "pop.w") and "pc" in fi.op_str:
                                break
                            if fi.mnemonic == "bx" and "lr" in fi.op_str:
                                break

# Also search for CRC32 computation by looking for the polynomial
# as immediate in EOR instructions
print("\n=== SEARCHING FOR CRC32 SOFTWARE COMPUTATION ===")
print("Looking for EOR with polynomial-like immediates...")
for chunk_start in range(0, 0x0A0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("eor", "eor.w", "eors", "eors.w"):
            if "#0x" in ins.op_str:
                # Extract the immediate
                try:
                    parts = ins.op_str.split("#")
                    for part in parts[1:]:
                        val_str = part.split(",")[0].strip()
                        val = int(val_str, 0)
                        if val > 0x10000:  # Large constant = likely polynomial
                            print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
                except:
                    pass

# Finally, look for flash write functions. On Ambiq Apollo4, flash operations 
# use the MSPI (Multi-SPI) peripheral at 0x40014000 or HAL functions.
# Search for MSPI base address references.
print("\n=== SEARCHING FOR FLASH/MSPI REFERENCES ===")
# Ambiq MSPI0: 0x40014000, MSPI1: 0x40015000, MSPI2: 0x40016000
for mspi_addr in [0x40014000, 0x40015000, 0x40016000]:
    target = struct.pack("<I", mspi_addr)
    pos = 0
    count = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        count += 1
        if count <= 3:
            print(f"  MSPI 0x{mspi_addr:08X} at offset 0x{pos:06X}")
        pos += 1
    if count > 3:
        print(f"  ... {count} total for MSPI 0x{mspi_addr:08X}")

