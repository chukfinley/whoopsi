#!/usr/bin/env python3
"""Find BLE command dispatch by examining TBB/TBH switch tables and their CMP guards."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

TBB_SITES = [0x010730, 0x010D0A, 0x0201D4, 0x020A1A, 0x020C68, 0x021088, 
             0x050216, 0x0705AE, 0x070F1E, 0x08013C]

for tbb_addr in TBB_SITES:
    # Disassemble 128 bytes before TBB to see the CMP that guards it
    start = max(0, tbb_addr - 128)
    instrs = list(md.disasm(data[start:tbb_addr+4], start))
    
    # Find the CMP that determines the switch range
    max_cmp = None
    for ins in instrs:
        if ins.mnemonic in ("cmp", "cmp.w"):
            if "#" in ins.op_str:
                try:
                    val = int(ins.op_str.split("#")[1].split(",")[0].strip(), 0)
                    max_cmp = (ins.address, val)
                except:
                    pass
    
    if max_cmp:
        print(f"TBB/TBH at 0x{tbb_addr:06X}: switch range CMP at 0x{max_cmp[0]:06X} = #0x{max_cmp[1]:X} ({max_cmp[1]})")
        # If max value >= 0x8E, this could be our firmware command dispatch
        if max_cmp[1] >= 0x8E:
            print(f"  *** POTENTIAL FIRMWARE COMMAND DISPATCH (range covers 0x8E+) ***")
    else:
        print(f"TBB/TBH at 0x{tbb_addr:06X}: no CMP found in preceding 128 bytes")

# Also search for TBB/TBH in the ENTIRE binary (not just first 640KB)
print("\n\nFull binary TBB/TBH scan...")
all_tbb = []
for chunk_start in range(0, len(data), 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("tbb", "tbh"):
            all_tbb.append(ins.address)

print(f"Total TBB/TBH: {len(all_tbb)}")
for addr in all_tbb:
    if addr not in TBB_SITES:
        # Check CMP before it
        start = max(0, addr - 128)
        instrs = list(md.disasm(data[start:addr+4], start))
        for ins in instrs:
            if ins.mnemonic in ("cmp", "cmp.w") and "#" in ins.op_str:
                try:
                    val = int(ins.op_str.split("#")[1].split(",")[0].strip(), 0)
                    if val >= 0x50:  # Only show large switch tables
                        print(f"  TBB/TBH at 0x{addr:06X}: CMP #{val} (0x{val:X})")
                except:
                    pass

# Also: search for SUB + CMP patterns that normalize command byte
# e.g., SUB Rn, #0x8E; CMP Rn, #3 (range check for 4 commands 0x8E-0x91)
print("\n\nSearching for SUB/SUBS with firmware command base values...")
for chunk_start in range(0, 0xA0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("sub", "subs", "sub.w", "subs.w"):
            if "#0x8e" in ins.op_str.lower() or "#0x8f" in ins.op_str.lower() or \
               "#0x90" in ins.op_str.lower() or "#142" in ins.op_str:
                print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# Search for the value 142 (0x8E) as any immediate operand
print("\nSearching for any instruction with immediate #0x8e or #142...")
for chunk_start in range(0, 0xA0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if "#0x8e" in ins.op_str.lower():
            print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

