#!/usr/bin/env python3
"""Phase 7: Understand the string referencing mechanism. 
Look at how the one known reference (0x0FEA54 -> 0x0B4AEB) is used."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# The pointer at 0x0FEA54 -> 0x0B4AEB. What's around 0x0FEA54?
# Is it in a data table or in code (literal pool)?
print("Context around 0x0FEA54:")
# Check if this is in a data region by looking at surrounding values
for off in range(0x0FEA40, 0x0FEA70, 4):
    val = struct.unpack_from("<I", data, off)[0]
    # Check if it looks like a string pointer
    note = ""
    if 0x0A0000 <= val <= 0x170000 and val < len(data):
        try:
            end = data.index(0, val)
            s = data[val:min(val+50, end)].decode('ascii')
            note = f"  -> \"{s[:50]}\""
        except:
            note = "  -> (not a string)"
    mark = " <<<" if off == 0x0FEA54 else ""
    print(f"  0x{off:06X}: 0x{val:08X}{note}{mark}")

# Let's also search UN-aligned (every byte position) for our string addresses
# Maybe they're packed differently
print("\n\nUnaligned search for key string address 0x0B4AE5 (CRC of update image passed):")
target = struct.pack("<I", 0x0B4AE5)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"  Found at offset 0x{pos:06X}")
    pos += 1

# Check: maybe the logging system uses string IDs/indices instead of pointers.
# Let's look at the structure around 0x0FEA54 more carefully.
# The ptr table at 0x074068 earlier pointed to strings. Let's scan for a 
# large table of string pointers.

print("\n\nSearching for large string pointer tables (0x0A0000-0x160000 range)...")
# Find runs of consecutive aligned values all pointing into string region
run_start = None
run_len = 0
best_runs = []

for off in range(0, len(data) - 3, 4):
    val = struct.unpack_from("<I", data, off)[0]
    is_str_ptr = 0x0A0000 <= val <= 0x160000 and val < len(data)
    if is_str_ptr:
        if run_start is None:
            run_start = off
            run_len = 1
        else:
            run_len += 1
    else:
        if run_len >= 8:
            best_runs.append((run_start, run_len))
        run_start = None
        run_len = 0

best_runs.sort(key=lambda x: -x[1])
print(f"Found {len(best_runs)} table runs (>= 8 entries)")
for start, length in best_runs[:20]:
    print(f"  Table at 0x{start:06X}: {length} entries (0x{start:06X}-0x{start+length*4:06X})")
    # Show first few
    for i in range(min(3, length)):
        off = start + i * 4
        val = struct.unpack_from("<I", data, off)[0]
        try:
            end = data.index(0, val)
            s = data[val:min(val+50, end)].decode('ascii', errors='replace')
            print(f"    [{i}] 0x{val:06X}: \"{s[:50]}\"")
        except:
            pass

# Now the key question: maybe the strings with "%6llu:" prefix are part of
# a structured logging system where the string is NOT directly referenced
# by pointer, but instead by a struct containing {file, line, string} or similar.
# Let's look for structures that contain our string addresses

# Actually, let's try: maybe the format is {u16 id, ...} and strings are 
# looked up by ID. Or maybe the strings are referenced by the OFFSET from 
# some base. Let's compute: 0x0FEA54 contains 0x0B4AEB. This is in a 
# data region around 0x0FE000. What else is there?

print("\n\nData structure around 0x0FEA40-0x0FEA70:")
for off in range(0x0FEA30, 0x0FEA80, 1):
    pass  # Already showed above

# Let's look at this from code side. Find code that references address near 0x0FEA54
# by searching for 0x0FEA54 as a literal pool value
target_ptr = struct.pack("<I", 0x0FEA54)
pos = 0
while True:
    pos = data.find(target_ptr, pos)
    if pos == -1: break
    print(f"\nPointer to 0x0FEA54 found at 0x{pos:06X}")
    pos += 1

# Try nearby table base addresses
for base_try in [0x0FEA00, 0x0FEA40, 0x0FE000, 0x0FE800]:
    target_base = struct.pack("<I", base_try)
    pos = 0
    while True:
        pos = data.find(target_base, pos)
        if pos == -1: break
        print(f"  Pointer to 0x{base_try:06X} found at 0x{pos:06X}")
        pos += 1

