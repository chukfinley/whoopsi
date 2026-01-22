#!/usr/bin/env python3
"""Disassemble CRC16 function and find all callers. Then trace up to FW update."""

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

# ============================================================
# 1. Full CRC16 function at 0x0127D8
# ============================================================
print("=== CRC16 FUNCTION at 0x0127D8 ===")
instrs = disasm(0x0127D8, 128)
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}")
    if ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
        break
    if ins.mnemonic == "bx" and "lr" in ins.op_str:
        break

# The function at 0x0127D8 is small - it's the CRC16 computation function
# Parameters: r0=data_ptr, r1=length (r0+r1=end), r2=initial_crc
# Returns: r0=computed_crc

# ============================================================
# 2. Find ALL callers of 0x0127D8 (BL #0x127D8 or BL #0x127D9)
# ============================================================
print("\n=== ALL CALLERS OF CRC16 FUNCTION (0x0127D8) ===")

# BL encoding in Thumb-2:
# BL target: encoded as relative offset in two halfwords
# We can search for all BL instructions that resolve to 0x0127D8/0x0127D9

callers = []
CHUNK = 0x10000
for chunk_start in range(0, len(data), CHUNK):
    chunk = data[chunk_start:min(chunk_start+CHUNK, len(data))]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic == "bl":
            try:
                target = int(ins.op_str.replace("#", ""), 0)
                if target == 0x0127D8 or target == 0x0127D9:
                    callers.append(ins.address)
                    print(f"  BL at 0x{ins.address:06X}")
            except:
                pass

print(f"\nTotal callers: {len(callers)}")

# ============================================================
# 3. For each caller, find its function and show context
# ============================================================
print("\n=== CALLER FUNCTION CONTEXT ===")
for caller_addr in callers:
    # Find function start
    fs = None
    for off in range(2, 8192, 2):
        p = caller_addr - off
        if p < 0: break
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
            fs = p
            break
    
    if fs:
        print(f"\n--- Function at 0x{fs:06X} (calls CRC16 at 0x{caller_addr:06X}) ---")
        func_instrs = disasm(fs, min(2048, caller_addr - fs + 256))
        
        # Show condensed: just BL calls and CMP/CBZ after the CRC call
        show_full = caller_addr - fs < 256  # Show full if small function
        
        if show_full:
            for ins in func_instrs:
                mark = " <<<CRC16" if ins.address == caller_addr else ""
                print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}{mark}")
                if ins.address > caller_addr + 100 and \
                   ins.mnemonic in ("pop","pop.w") and "pc" in ins.op_str:
                    break
                if len([1 for i in func_instrs if i.address <= ins.address]) > 80:
                    break
        else:
            # Just show calls and key instructions
            print(f"  (function is large, showing calls and key instructions)")
            for ins in func_instrs:
                if ins.mnemonic in ("bl", "blx"):
                    target = ins.op_str.replace("#","")
                    crc_mark = " <<<CRC16" if ins.address == caller_addr else ""
                    print(f"  0x{ins.address:06X}: {ins.mnemonic} {target}{crc_mark}")
                elif ins.mnemonic in ("cmp", "cmp.w", "cbz", "cbnz") and \
                     abs(ins.address - caller_addr) < 20:
                    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
    else:
        print(f"\n--- Could not find function for caller at 0x{caller_addr:06X} ---")

# ============================================================
# 4. Also find callers of callers (2 levels up) for the most 
#    interesting ones
# ============================================================

# Let's also look for a CRC32 function. Since no polynomial was found as immediate,
# it might use a table-based CRC32. Let's search for a 1KB (256*4=1024 byte) table
# of plausible CRC32 values.
print("\n=== SEARCHING FOR CRC32 TABLE ===")
# A CRC32 table starts with 0x00000000 and has entries like 0x77073096
for off in range(0, len(data) - 1024, 4):
    v0 = struct.unpack_from("<I", data, off)[0]
    v1 = struct.unpack_from("<I", data, off+4)[0]
    if v0 == 0x00000000 and v1 == 0x77073096:  # CRC-32 reflected
        print(f"  CRC-32 table at 0x{off:06X}")
    if v0 == 0x00000000 and v1 == 0x04C11DB7:  # CRC-32 normal
        print(f"  CRC-32 (normal) table at 0x{off:06X}")
    # Also check for CRC-32C  
    if v0 == 0x00000000 and v1 == 0xF26B8303:
        print(f"  CRC-32C table at 0x{off:06X}")

