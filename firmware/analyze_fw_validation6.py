#!/usr/bin/env python3
"""Phase 6: Find string pointer tables containing our CRC/firmware strings."""

import struct

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

# Search for ANY 4-byte aligned pointer that points to our target strings
TARGETS = [0x0AC742, 0x0AC749, 0x0B4AE5, 0x0B4B0C, 0x0B4842, 0x0B38C6, 
           0x0B3938, 0x0B4ACB, 0x0B49E0, 0x0B5A6F, 0x0B616A, 0x0B5F25, 
           0x0B5F5A, 0x0B5E5F, 0x0B4A40, 0x0B3905, 0x0B3915, 0x0B397E,
           0x0B5303, 0x0B4872, 0x0B4896, 0x0B48C8, 0x0B4959, 0x0B4A9A,
           0x0B4B42, 0x0B4B94]
target_set = set(TARGETS)

# Also search for pointers to nearby string addresses (within +-20 bytes)
expanded_targets = set()
for t in TARGETS:
    for delta in range(-20, 21):
        expanded_targets.add(t + delta)

print("Searching for pointers to target strings (4-byte aligned scan)...")
for off in range(0, len(data) - 3, 4):
    val = struct.unpack_from("<I", data, off)[0]
    if val in expanded_targets:
        # Verify it's actually pointing to a string
        if val < len(data):
            try:
                end = data.index(0, val)
                s = data[val:end].decode('ascii')
                if len(s) > 2 and s.isprintable():
                    exact = "EXACT" if val in target_set else "near"
                    print(f"  0x{off:06X}: -> 0x{val:06X} [{exact}] \"{s[:70]}\"")
            except:
                pass

# Also search for pointers to "Firmware Load (0x8E)" at 0x0B4842
# and surrounding firmware update strings
print("\n\nSearching more broadly for pointers into firmware update string region 0x0B3800-0x0B5400...")
count = 0
for off in range(0, len(data) - 3, 4):
    val = struct.unpack_from("<I", data, off)[0]
    if 0x0B3800 <= val <= 0x0B5400:
        if val < len(data):
            try:
                end = data.index(0, val)
                s = data[val:min(val+80, end)].decode('ascii')
                if len(s) > 3:
                    count += 1
                    if count <= 60:
                        print(f"  0x{off:06X}: -> 0x{val:06X} \"{s[:70]}\"")
            except:
                pass
if count > 60:
    print(f"  ... {count} total")

