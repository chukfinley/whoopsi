#!/usr/bin/env python3
"""Phase 4: Base address is 0. Find string refs with that knowledge."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

BASE = 0  # Load address = 0, file offset = memory address

# Verify: reset vector 0x4A4D9 (Thumb), code at 0x4A4D8
# Should see valid Thumb instructions there
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True
print("Reset handler (0x4A4D8):")
for i in md.disasm(data[0x4A4D8:0x4A4D8+32], 0x4A4D8):
    print(f"  0x{i.address:06X}: {i.mnemonic} {i.op_str}")

# Now: strings at e.g. 0x0AC749. In code, the string address 0x0AC749 should
# appear in a literal pool. But earlier we searched for it and found nothing.
# Maybe strings have a different addressing? Let's check if there's a prefix.
# The strings we found at 0x0AC749 - maybe the actual referenced addr has format
# "%6llu: " prefix. Let's look at what's before the string.
print("\nContext around 'Update image CRC valid' string:")
print(f"  {data[0x0AC730:0x0AC770]}")

# The string at 0x0AC742 is "%6llu: Update image CRC valid: %s"
# So the full string starts at 0x0AC742, and the code references 0x0AC742
str_addr = 0x0AC742
target = struct.pack("<I", str_addr)
refs = []
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    refs.append(pos)
    pos += 1
print(f"\nLiteral pool entries for 0x{str_addr:06X}: {[f'0x{r:06X}' for r in refs]}")

# Try the full prefixed string
str_addr2 = 0x0AC749
target2 = struct.pack("<I", str_addr2)
refs2 = []
pos = 0
while True:
    pos = data.find(target2, pos)
    if pos == -1: break
    refs2.append(pos)
    pos += 1
print(f"Literal pool entries for 0x{str_addr2:06X}: {[f'0x{r:06X}' for r in refs2]}")

# Maybe the string addresses are offset by the 0x200 header?
# i.e., the actual image is loaded at some base, and string offsets in code
# account for that. Let's try: what if the code/data after offset 0x200 is
# loaded at 0x0004A200 (so that vector table at +0x200 is at 0x4A400)?
# No, that doesn't work either.

# Let's try a different approach: find ALL 32-bit values in the binary that
# point into the string region (0x0A0000-0x170000) and see what pattern emerges.

# Actually, let me check the Ambiq image header. Offset 0x4 = 0x00179ED8.
# This could be the image size or a CRC. 0x200 offset for vector table.
# The first word 0x6EB0D692 could be the Ambiq magic.

# Let me search for 0x6EB0D692 as a magic value in the binary
magic = struct.pack("<I", 0x6EB0D692)
print(f"\nSearching for header magic 0x6EB0D692:")
pos = 0
while True:
    pos = data.find(magic, pos)
    if pos == -1: break
    print(f"  Found at 0x{pos:06X}")
    pos += 1

# Let me try: maybe literal pools use addresses relative to a different base.
# Let's scan for any 4-byte LE value that, when used as a literal pool load,
# would point to the region of our CRC strings (0x0AC700-0x0AC800).
# Search for any value 0x000AC700 - 0x000AC800 stored as 32-bit LE
print("\nSearching for any 32-bit pointer into 0x0AC700-0x0B5000 range:")
count = 0
for off in range(0, len(data)-3, 4):  # Aligned search
    val = struct.unpack_from("<I", data, off)[0]
    if 0x0AC700 <= val <= 0x0B5000:
        if count < 20:
            print(f"  0x{off:06X}: -> 0x{val:08X}")
        count += 1
if count > 20:
    print(f"  ... {count} total pointers found")
print(f"Total: {count}")

# Also try with +0x200 offset (if strings are at file_offset but code uses file_offset+0x200?)
print("\nSearching for pointers to strings with +0x200 offset:")
count2 = 0
for off in range(0, len(data)-3, 4):
    val = struct.unpack_from("<I", data, off)[0]
    if 0x0ACF00 <= val <= 0x0B5200:
        if count2 < 20:
            print(f"  0x{off:06X}: -> 0x{val:08X}")
        count2 += 1
print(f"Total: {count2}")

