#!/usr/bin/env python3
"""Phase 8: Direct approach - find CRC validation and FW update code by:
1. Finding the CRC16 computation function (references CRC16 table)
2. Finding BLE command dispatch (0x8E-0x91 comparisons)
3. Finding code near the string table at 0x074068
4. Searching for the Ambiq OTA update pattern
"""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

def disasm_func(start, max_len=1024):
    instrs = list(md.disasm(data[start:start+max_len], start))
    return instrs

def print_func(instrs, max_lines=100):
    for i, ins in enumerate(instrs):
        if i >= max_lines:
            print(f"  ... ({len(instrs)-max_lines} more)")
            break
        print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}")

def find_func_start(addr, max_back=4096):
    for off in range(2, max_back, 2):
        p = addr - off
        if p < 0: break
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500:
            return p
        if hw == 0xE92D:
            return p
    return None

# ============================================================
# PART 1: Find CRC16 function by locating code that references 
# the CRC16 table at 0x0AC3B4
# ============================================================
print("=" * 70)
print("PART 1: CRC16 Table References (table at 0x0AC3B4)")
print("=" * 70)

crc16_table = 0x0AC3B4
target = struct.pack("<I", crc16_table)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"\nLiteral pool entry at 0x{pos:06X} -> CRC16 table")
    # Find LDR that uses this literal pool
    # Scan backward up to 1024 bytes for code that loads from here
    func_start = find_func_start(pos)
    if func_start:
        print(f"Function containing this reference starts at 0x{func_start:06X}")
        instrs = disasm_func(func_start, 512)
        print_func(instrs, 80)
    pos += 1

# ============================================================
# PART 2: Find BLE command dispatch - scan for CMP #0x8E thru #0x91
# These are Thumb instructions: CMP Rn, #imm8
# Encoding: 0x28nn where nn = imm and top 3 bits of Rn
# Actually CMP Rn, #imm8 = 0010 1nnn iiiiiiii
# ============================================================
print("\n" + "=" * 70)
print("PART 2: BLE Command Dispatch (CMP with 0x8E-0x91)")
print("=" * 70)

# Scan for specific byte patterns:
# CMP R0-R7, #0x8E = 28 8E, 29 8E, 2A 8E, etc (for R0=0x28, R1=0x29...)
# Also CMP.W can encode these
fw_cmds = {0x8E: "START_FIRMWARE_LOAD", 0x8F: "LOAD_FW_DATA", 
            0x90: "PROCESS_FIRMWARE_IMAGE", 0x91: "VERIFY_FW_IMAGE",
            0x92: "SET_CLOCK"}

all_cmp_hits = []
CHUNK = 0x20000
for chunk_start in range(0, min(len(data), 0x0A0000), CHUNK):
    chunk_end = min(chunk_start + CHUNK, len(data))
    region = data[chunk_start:chunk_end]
    for ins in md.disasm(region, chunk_start):
        if ins.mnemonic in ("cmp", "cmp.w"):
            for cmd_val, cmd_name in fw_cmds.items():
                if f"#0x{cmd_val:x}" in ins.op_str:
                    all_cmp_hits.append((ins.address, ins.mnemonic, ins.op_str, cmd_val, cmd_name))

print(f"Found {len(all_cmp_hits)} CMP instructions with FW command values:")
for addr, mnem, ops, val, name in all_cmp_hits:
    print(f"  0x{addr:06X}: {mnem} {ops}  [{name}]")

# For each unique function containing these CMPs, disassemble
seen = set()
for addr, mnem, ops, val, name in all_cmp_hits:
    fs = find_func_start(addr)
    if fs and fs not in seen:
        seen.add(fs)
        print(f"\n--- Function at 0x{fs:06X} (contains CMP {ops} [{name}]) ---")
        instrs = disasm_func(fs, 2048)
        # Show up to the CMP and some context after
        trimmed = []
        for ins in instrs:
            trimmed.append(ins)
            if ins.address > addr + 200 and (
                (ins.mnemonic in ("pop","pop.w") and "pc" in ins.op_str) or
                (ins.mnemonic == "bx" and "lr" in ins.op_str)):
                break
            if len(trimmed) > 200:
                break
        print_func(trimmed, 200)
        
        # Show all BL calls in this function
        print("  Calls made:")
        for ins in trimmed:
            if ins.mnemonic in ("bl", "blx"):
                print(f"    0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# ============================================================
# PART 3: Find Ambiq-specific OTA patterns
# Search for the header magic 0x6EB0D692 references in code
# ============================================================
print("\n" + "=" * 70)
print("PART 3: Ambiq Image Header Magic (0x6EB0D692)")
print("=" * 70)

magic_val = 0x6EB0D692
target = struct.pack("<I", magic_val)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"  Magic at 0x{pos:06X}")
    # If in code region, find the function
    if pos < 0x0A0000:  # likely code region
        fs = find_func_start(pos)
        if fs:
            print(f"  -> In function at 0x{fs:06X}")
            instrs = disasm_func(fs, 512)
            print_func(instrs, 60)
    pos += 1

# ============================================================
# PART 4: Search for CRC32 computation patterns
# CRC32 polynomial 0xEDB88320 (reflected) or 0x04C11DB7
# ============================================================
print("\n" + "=" * 70)
print("PART 4: CRC32 Polynomial Search")
print("=" * 70)

for poly, name in [(0xEDB88320, "CRC32 reflected"), (0x04C11DB7, "CRC32 normal"),
                    (0x1EDC6F41, "CRC32-C"), (0x82F63B78, "CRC32-C reflected")]:
    target = struct.pack("<I", poly)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  {name} (0x{poly:08X}) found at 0x{pos:06X}")
        if pos < 0x0A0000:
            fs = find_func_start(pos)
            if fs:
                print(f"    In function at 0x{fs:06X}")
                instrs = disasm_func(fs, 256)
                print_func(instrs, 40)
        pos += 1

# ============================================================
# PART 5: Find the actual logging/string reference mechanism
# Look at the string table at 0x074068 and trace backward
# ============================================================
print("\n" + "=" * 70)  
print("PART 5: Tracing how strings at 0x074068 table are accessed")
print("=" * 70)

# Find code that references the table base 0x074068
table_addr = 0x074068
target = struct.pack("<I", table_addr)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"  Reference to string table at 0x{pos:06X}")
    pos += 1

# Try nearby base addresses
for try_addr in range(0x074000, 0x074070, 4):
    target = struct.pack("<I", try_addr)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        if pos < 0x0A0000:  # in code region
            print(f"  Ptr to 0x{try_addr:06X} at code offset 0x{pos:06X}")
        pos += 1

print("\nDone.")
