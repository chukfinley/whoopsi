#!/usr/bin/env python3
"""Phase 5: Scan all LDR [PC, #imm] to find which load our string addresses."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

# Target string addresses
TARGETS = {
    0x0AC742: "Update image CRC valid: %s (with prefix)",
    0x0B4AE5: "CRC of update image passed",
    0x0B4B0C: "CRC of update image failed",
    0x0B4842: "Firmware Load (0x8E)",
    0x0B38C6: "Verify FW image chunk offset 0x0 %s",
    0x0B3938: "VERIFY_FW_IMAGE unsupported revision",
    0x0B4ACB: "Image (0x90).",
    0x0B49E0: "Firmware image load succeeded",
    0x0B5A6F: "Verify FW image chunk offset 0x%x %s",
    0x0B616A: "header Valid, Magic 0x%08lx",
    0x0B5F25: "crc32 for flash header",
    0x0B5F5A: "crc32 mismatch",
    0x0B5E5F: "valid header crc Pkt: %04x Calc: %04x",
}

# Scan the entire binary for LDR instructions that resolve to our target addresses.
# We process in chunks to manage memory.
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

CHUNK = 0x20000
found = {}  # {string_addr: [code_addr, ...]}

for chunk_start in range(0, len(data), CHUNK):
    chunk_end = min(chunk_start + CHUNK + 4096, len(data))  # overlap for literal pools
    region = data[chunk_start:chunk_end]
    
    for i in md.disasm(region, chunk_start):
        if i.address >= chunk_start + CHUNK:
            break
        if i.mnemonic in ("ldr", "ldr.w"):
            # Check for PC-relative load
            for op in i.operands:
                if op.type == 4 and op.mem.base == 15:  # MEM with PC base
                    disp = op.mem.disp
                    pool_addr = ((i.address + 4) & ~3) + disp
                    # Read the value at pool_addr
                    if 0 <= pool_addr < len(data) - 3:
                        loaded_val = struct.unpack_from("<I", data, pool_addr)[0]
                        if loaded_val in TARGETS:
                            if loaded_val not in found:
                                found[loaded_val] = []
                            found[loaded_val].append(i.address)

print("String references found via LDR [PC, #imm] -> literal pool -> string addr:")
print("=" * 70)
for str_addr in sorted(found.keys()):
    print(f"\n  0x{str_addr:06X}: \"{TARGETS[str_addr]}\"")
    for ca in found[str_addr]:
        print(f"    <- code at 0x{ca:06X}")

if not found:
    print("  None found! Strings may be loaded via different mechanism.")
    
    # Let's check how strings ARE loaded. Pick a known pointer from earlier 
    # analysis (the 67 pointers we found). Those pointed to code addrs. 
    # Let's look for string table indirection.
    # 
    # Alternative: scan for ADR instructions
    print("\n\nScanning for ADR instructions resolving to target strings...")
    for chunk_start in range(0, len(data), CHUNK):
        chunk_end = min(chunk_start + CHUNK + 4096, len(data))
        region = data[chunk_start:chunk_end]
        for i in md.disasm(region, chunk_start):
            if i.address >= chunk_start + CHUNK:
                break
            if i.mnemonic in ("adr", "adr.w", "add", "add.w", "sub", "sub.w"):
                # Check for PC-relative
                if "pc" in i.op_str.lower():
                    for op in i.operands:
                        if op.type == 2:  # IMM
                            # Various ways PC-relative adds work
                            effective = ((i.address + 4) & ~3) + op.imm
                            if effective in TARGETS:
                                print(f"  ADR at 0x{i.address:06X} -> 0x{effective:06X}: \"{TARGETS[effective]}\"")

    # Final attempt: maybe there's a log function wrapper and strings are in 
    # a table. Let's look for the string pointer at offset 0x074068 mentioned.
    print("\n\nChecking string pointer table at 0x074068:")
    for off in range(0x074000, min(0x074200, len(data)-3), 4):
        val = struct.unpack_from("<I", data, off)[0]
        if 0x0A0000 <= val <= 0x170000:
            # Check if it points to a printable string
            if val < len(data):
                s = data[val:val+40]
                try:
                    decoded = s[:s.index(0)].decode('ascii')
                    if len(decoded) > 3:
                        print(f"  0x{off:06X}: -> 0x{val:06X} \"{decoded[:60]}\"")
                except:
                    pass

