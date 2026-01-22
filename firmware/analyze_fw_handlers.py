#!/usr/bin/env python3
"""Examine the large function pointer tables for BLE command dispatch."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

def disasm(start, length=512):
    return list(md.disasm(data[start:start+length], start))

def print_insns(instrs, n=80):
    for i, ins in enumerate(instrs[:n]):
        print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}")

# Table at 0x04405C has 119 entries. If these are command handlers indexed 0-118,
# command 0x8E (142) is out of range (119 < 142).
# But maybe the index is offset. If commands start at some base value:
# e.g., if base=0x80, then 0x8E = index 14, 0x8F = index 15, etc.

# Let's examine ALL tables and check which one might handle firmware commands
TABLES = [
    (0x04405C, 119),
    (0x0442BC, 91),
    (0x000288, 66),  # This starts at 0x288 = right after 0x200 vector table + 0x88
    (0x03837C, 60),
    (0x03B2A0, 52),
    (0x05337C, 49),
    (0x02E740, 45),
]

# Table at 0x000288: 66 entries. Interesting - at offset 0x288, right after headers.
# Could be the main interrupt/dispatch vector table supplement.
# But all point to 0x04A541 (same handler) - this is the default exception vector table.

# Let's look at how the largest table (119 entries at 0x04405C) is referenced.
# Find code that loads 0x04405C
print("=== REFERENCES TO TABLE BASE ADDRESSES ===")
for table_addr, count in TABLES:
    target = struct.pack("<I", table_addr)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  Table 0x{table_addr:06X} ({count} entries) referenced at 0x{pos:06X}")
        # Disassemble context
        fs = None
        for off in range(2, 4096, 2):
            p = pos - off
            if p < 0: break
            hw = struct.unpack_from("<H", data, p)[0]
            if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
                fs = p
                break
        if fs:
            print(f"    In function at 0x{fs:06X}:")
            instrs = disasm(fs, min(512, pos - fs + 64))
            print_insns(instrs, 40)
        pos += 1

# Let's look at the 119-entry table more carefully.
# If it's indexed by BLE command byte, what are entries at common indices?
print("\n=== TABLE AT 0x04405C (119 entries) ===")
print("Checking specific indices:")
table = 0x04405C
for idx in range(119):
    off = table + idx * 4
    val = struct.unpack_from("<I", data, off)[0]
    target = val & ~1
    # Only print non-default entries (ones that differ from the "default" handler)
    default = struct.unpack_from("<I", data, table + 3*4)[0]  # Use index 3 as default
    if val != default or idx < 5 or idx in [0x0E, 0x0F, 0x10, 0x11]:
        # Get first instruction at target
        hw = struct.unpack_from("<H", data, target)[0] if target < len(data)-1 else 0
        is_push = (hw & 0xFF00) == 0xB500 or hw == 0xE92D
        print(f"  [{idx:3d}/0x{idx:02X}] -> 0x{target:06X} {'PUSH' if is_push else ''}")

# Check if any entries in the 91-entry table map to firmware handlers
print("\n=== TABLE AT 0x0442BC (91 entries) ===")
table2 = 0x0442BC
default2 = struct.unpack_from("<I", data, table2 + 2*4)[0]
for idx in range(91):
    off = table2 + idx * 4
    val = struct.unpack_from("<I", data, off)[0]
    if val != default2 or idx < 5:
        target = val & ~1
        print(f"  [{idx:3d}/0x{idx:02X}] -> 0x{target:06X}")

