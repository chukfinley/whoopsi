#!/usr/bin/env python3
"""FINAL: The binary has base=0 and code IS valid Thumb-2. 
The reason we couldn't find string references is that the strings are NOT 
referenced by 32-bit pointers in literal pools - they're likely part of a 
compressed/indexed logging system.

Strategy:
1. Find all functions by scanning for PUSH prologues
2. For CRC validation: find functions that call CRC calculation then branch on result
3. Trace BLE command handlers by finding dispatch tables (switch/case on command byte)
4. Look at the code at 0x183C0 which compares values like 0xE1, 0xD4 - looks like command dispatch
"""

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

def print_insns(instrs, n=100):
    for i, ins in enumerate(instrs[:n]):
        print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}")

def find_func_start(addr):
    for off in range(2, 8192, 2):
        p = addr - off
        if p < 0: break
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
            return p
    return None

# ============================================================
# 1. The function at 0x183C0 looks like a BLE command dispatcher
#    (comparing command byte to 0xE1, 0xD4, etc.)
#    0x8E = 142, 0x90 = 144, 0x91 = 145
#    Let's fully disassemble it and see the dispatch logic
# ============================================================
print("=" * 70)
print("1. BLE COMMAND DISPATCHER at 0x183C0")
print("=" * 70)

instrs = disasm(0x183C0, 4096)
# Show a good portion - this function dispatches many commands
printed = 0
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}")
    printed += 1
    # Stop at return after seeing many CMPs
    if printed > 10 and ins.mnemonic in ("pop","pop.w") and "pc" in ins.op_str:
        break
    if printed > 300:
        break

# Show all CMP instructions in this function
print("\n  All CMP values in this function:")
for ins in instrs[:300]:
    if ins.mnemonic in ("cmp", "cmp.w"):
        val = None
        for op in ins.operands:
            if op.type == 2:  # IMM
                val = op.imm
        if val is not None:
            name = ""
            if val == 0x8E: name = " [START_FIRMWARE_LOAD]"
            elif val == 0x8F: name = " [LOAD_FW_DATA]"
            elif val == 0x90: name = " [PROCESS_FIRMWARE_IMAGE]"
            elif val == 0x91: name = " [VERIFY_FW_IMAGE]"
            elif val == 0x92: name = " [SET_CLOCK]"
            print(f"    0x{ins.address:06X}: {ins.mnemonic} {ins.op_str} (={val}/0x{val:02X}){name}")

# ============================================================
# 2. Follow BL targets from the dispatch function for FW commands
# ============================================================
print("\n" + "=" * 70)
print("2. BL TARGETS (function calls) from dispatcher")
print("=" * 70)

for ins in instrs[:300]:
    if ins.mnemonic == "bl":
        target = int(ins.op_str.replace("#", ""), 0)
        print(f"  0x{ins.address:06X}: bl 0x{target:06X}")

# ============================================================
# 3. Look for CRC-related code patterns:
#    - XOR with polynomial
#    - Table lookups (LDR with index shift)
#    - Functions that return 0/1 (validation)
# Search for the literal pool value that IS the CRC16 table addr
# We know the table is at 0xAC3B4. With base=0, search for that exact value.
# ============================================================
print("\n" + "=" * 70)
print("3. CRC16 TABLE REFERENCES")
print("=" * 70)

# The CRC16 table at 0xAC3B4 - search for this as a literal pool value
# But we already tried this. Let's search byte-by-byte (unaligned too)
for search_val in [0x0AC3B4, 0x0AC3B0, 0x0AC3B2]:
    target = struct.pack("<I", search_val)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  Value 0x{search_val:06X} at offset 0x{pos:06X}")
        # Show surrounding code
        fs = find_func_start(pos)
        if fs:
            print(f"    Function at 0x{fs:06X}")
        pos += 1

# Maybe the table is loaded via MOVW/MOVT pair (split into two 16-bit immediates)
# 0x0AC3B4 -> MOVW #0xC3B4, MOVT #0x000A
# MOVW encoding: f240 + imm16 split across bytes
# MOVT encoding: f2c0 + imm16 split across bytes
# Let's search for MOVW with lower 16 bits = 0xC3B4
# MOVW Rd, #imm16: 1111 0i10 0100 imm4 : 0 imm3 Rd imm8
# For #0xC3B4: i=1 imm4=0000 imm3=100 imm8=10110100
# Hmm complex encoding. Let's just disassemble and search.

print("\nSearching for MOVW #0xc3b4 (lower half of CRC16 table addr)...")
CHUNK = 0x20000
for chunk_start in range(0, 0x0A0000, CHUNK):
    chunk_end = min(chunk_start + CHUNK, len(data))
    for ins in md.disasm(data[chunk_start:chunk_end], chunk_start):
        if ins.mnemonic in ("movw", "mov.w", "movt", "movt.w"):
            if "#0xc3b4" in ins.op_str.lower() or "#0xac3" in ins.op_str.lower():
                print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# Also search for upper half
print("\nSearching for MOVT #0xa or MOVT #0xb (upper halves for string/table addrs)...")
for chunk_start in range(0, 0x0A0000, CHUNK):
    chunk_end = min(chunk_start + CHUNK, len(data))
    for ins in md.disasm(data[chunk_start:chunk_end], chunk_start):
        if ins.mnemonic in ("movt", "movt.w"):
            for op in ins.operands:
                if op.type == 2 and op.imm in (0xa, 0xb, 0xc):
                    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
                    break

