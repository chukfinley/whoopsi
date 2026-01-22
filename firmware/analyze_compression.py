#!/usr/bin/env python3
"""Check if the binary contains compressed/encrypted sections alongside plain text."""

import struct, math
from collections import Counter

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

def entropy(chunk):
    if not chunk: return 0
    c = Counter(chunk)
    n = len(chunk)
    return -sum((v/n) * math.log2(v/n) for v in c.values())

# Detailed entropy map
print("Entropy map (4KB blocks, scale 0-8 bits):")
print(f"{'Offset':>10} {'Entropy':>8} {'Visual':>40} {'Type'}")
for off in range(0, len(data), 0x1000):
    chunk = data[off:off+0x1000]
    e = entropy(chunk)
    bar = '#' * int(e * 5)
    
    if e > 7.9: typ = "ENCRYPTED/COMPRESSED"
    elif e > 7.0: typ = "HIGH ENTROPY"
    elif e > 5.0: typ = "CODE"
    elif e > 3.0: typ = "DATA/STRINGS"  
    elif e > 1.0: typ = "SPARSE"
    else: typ = "NEAR-EMPTY"
    
    if off % 0x10000 == 0:  # Only print every 64KB boundary with summary
        # Compute average entropy for 64KB block
        block = data[off:off+0x10000]
        be = entropy(block)
        bar = '#' * int(be * 5)
        print(f"  0x{off:06X}  {be:6.3f}  {bar:40s} {typ}")

# Check for compression magic bytes
print("\n\nSearching for compression signatures...")
signatures = [
    (b"\x1f\x8b", "gzip"),
    (b"\x78\x9c", "zlib default"),
    (b"\x78\x01", "zlib no compression"),
    (b"\x78\xda", "zlib best compression"),
    (b"\xfd\x37\x7a\x58\x5a", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"LZ4", "LZ4"),
    (b"\x89\x4c\x5a\x4f", "LZO"),
    (b"BZ", "bzip2"),
    (b"\x04\x22\x4d\x18", "LZ4 frame"),
]

for sig, name in signatures:
    pos = 0
    while True:
        pos = data.find(sig, pos)
        if pos == -1: break
        print(f"  {name} signature at 0x{pos:06X}")
        pos += 1

# The key insight: we have 3475 valid PUSH prologues across the binary.
# If the code were compressed, we wouldn't decode valid PUSH instructions.
# So the code IS real - but the command dispatch uses a DIFFERENT mechanism.
# 
# Perhaps the BLE command bytes are dispatched through a function pointer table
# indexed by command byte. Let's look for a large table of function pointers.

print("\n\nSearching for function pointer tables (Thumb pointers = odd values)...")
from capstone import *
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

# Find runs of odd 32-bit values in code range
run_start = None
run_count = 0
best_runs = []
for off in range(0, len(data)-3, 4):
    val = struct.unpack_from("<I", data, off)[0]
    # Thumb function pointer: odd, in code range, and reasonable
    is_fptr = (val & 1) == 1 and 0x200 < val < 0xA0000
    if is_fptr:
        if run_start is None:
            run_start = off
            run_count = 1
        else:
            run_count += 1
    else:
        if run_count >= 10:
            best_runs.append((run_start, run_count))
        run_start = None
        run_count = 0

best_runs.sort(key=lambda x: -x[1])
print(f"Found {len(best_runs)} function pointer tables (>= 10 entries)")
for start, count in best_runs[:15]:
    print(f"\n  Table at 0x{start:06X}: {count} entries")
    for i in range(min(count, 5)):
        off = start + i*4
        val = struct.unpack_from("<I", data, off)[0]
        target = val & ~1
        # Check if target looks like valid code
        hw = struct.unpack_from("<H", data, target)[0] if target < len(data)-1 else 0
        is_push = (hw & 0xFF00) == 0xB500 or hw == 0xE92D
        print(f"    [{i:3d}] 0x{val:08X} -> 0x{target:06X} {'(PUSH)' if is_push else ''}")
    if count > 5:
        # Also show entries that might be at index 0x8E
        idx_8E = 0x8E
        if idx_8E < count:
            off = start + idx_8E*4
            val = struct.unpack_from("<I", data, off)[0]
            print(f"    [0x8E] 0x{val:08X} -> 0x{val&~1:06X}")

