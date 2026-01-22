#!/usr/bin/env python3
"""Phase 3: Determine load address and find string references."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

# Vector table is at offset 0x200. Read initial SP and reset vector.
sp_init = struct.unpack_from("<I", data, 0x200)[0]
reset_vec = struct.unpack_from("<I", data, 0x204)[0]
print(f"Initial SP:    0x{sp_init:08X}")
print(f"Reset vector:  0x{reset_vec:08X}")

# The reset vector address tells us the load base.
# If reset vector points to code at offset ~0x2XX in the binary,
# then base = reset_vec - offset_of_that_code
# Ambiq Apollo4 typically loads at 0x00018000 (MRAM) or 0x00060000
print(f"\nIf base=0x00018000, reset code at file offset 0x{reset_vec - 0x00018000:06X}")
print(f"If base=0x00060000, reset code at file offset 0x{reset_vec - 0x00060000:06X}")
print(f"If base=0x00000000, reset code at file offset 0x{reset_vec:06X}")

# Read more vectors to confirm
for i in range(16):
    v = struct.unpack_from("<I", data, 0x200 + i*4)[0]
    print(f"  Vector[{i:2d}] = 0x{v:08X}")

# Check what's at offset 0 (before vector table)
print(f"\nFirst 32 bytes of file:")
print(f"  {data[:32].hex()}")
print(f"\nBytes at 0x200 (vector table):")
print(f"  {data[0x200:0x220].hex()}")

# The 0x200 bytes before the vector table might be an image header
# Check for Ambiq image header magic
print(f"\nFirst 16 bytes (potential header):")
for i in range(0, 64, 4):
    v = struct.unpack_from("<I", data, i)[0]
    print(f"  Offset 0x{i:03X}: 0x{v:08X}")

