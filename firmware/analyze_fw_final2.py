#!/usr/bin/env python3
"""The binary appears to have VERY sparse valid code regions and dense string/data 
regions. The strings exist in plain text but code can't reference them normally.

Theory: This is a COMPOSITE image containing:
1. A bootloader/SBL section (the actual code near offset 0)  
2. The main firmware image (possibly compressed/encrypted) containing the strings

Let's analyze the structure more carefully."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

# Check the Ambiq image header at offset 0
print("=== AMBIQ IMAGE HEADER (offset 0x000) ===")
print(f"  Magic:        0x{struct.unpack_from('<I', data, 0)[0]:08X}")
print(f"  Field@0x04:   0x{struct.unpack_from('<I', data, 4)[0]:08X} ({struct.unpack_from('<I', data, 4)[0]})")
print(f"  Field@0x08:   0x{struct.unpack_from('<I', data, 8)[0]:08X}")
print(f"  Field@0x0C:   0x{struct.unpack_from('<I', data, 0xC)[0]:08X}")
print(f"  Field@0x10:   0x{struct.unpack_from('<I', data, 0x10)[0]:08X}")

# Check for a SECOND image header (the magic 0x6EB0D692 also at 0x1FC)
print(f"\n=== SECOND MAGIC at 0x1FC ===")
for i in range(0x1F0, 0x220, 4):
    v = struct.unpack_from('<I', data, i)[0]
    print(f"  0x{i:03X}: 0x{v:08X}")

# The Ambiq SBL (Secure Boot Loader) image format has:
# - Image header with magic, size, CRC, etc.
# - Then the actual ARM image with vector table
# The magic 0x6EB0D692 is NOT a standard Ambiq magic. 
# Standard Ambiq: 0xC0 at offset 0 or AM_IMAGE_MAGIC.

# Let's look for the Ambiq am_image_hdr_common_t structure
# which has: blobSize, crc, authAlgo, authKeyIdx, encAlgo, etc.
# The field at 0x04 (0x00179ED8) could be the total size of the image payload
# 0x179ED8 = 1,548,008 bytes + 0x200 header = 1,548,208 (close to file size 1,548,504)

# Actually, let me check what's at the VERY end of the file
print(f"\n=== END OF FILE ===")
print(f"  File size: {len(data)} (0x{len(data):06X})")
print(f"  Last 64 bytes:")
for i in range(len(data)-64, len(data), 4):
    v = struct.unpack_from('<I', data, i)[0]
    print(f"    0x{i:06X}: 0x{v:08X}")

# Header says 0x179ED8. File size is 0x17A1D8. Difference = 0x300.
# 0x300 = 0x200 (header) + 0x100 (trailer/padding?)
# So: 0x200 header + 0x179ED8 payload + 0x100 trailer = 0x17A1D8
# Nope: 0x200 + 0x179ED8 = 0x17A0D8, and 0x17A1D8 - 0x17A0D8 = 0x100

# Check what's at 0x17A0D8 (end of payload per header)
print(f"\n=== AT PAYLOAD END (0x17A0D8) ===")
for i in range(0x17A0D0, min(0x17A100, len(data)), 4):
    v = struct.unpack_from('<I', data, i)[0]
    print(f"  0x{i:06X}: 0x{v:08X}")

# Now let's approach differently: find ALL function prologues and map them
print("\n=== FUNCTION PROLOGUE MAP (first 20 per region) ===")
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

prologues = []
for off in range(0, len(data)-1, 2):
    hw = struct.unpack_from("<H", data, off)[0]
    if (hw & 0xFF00) == 0xB500:  # PUSH {... LR}
        prologues.append(off)
    elif hw == 0xE92D and off + 2 < len(data):  # PUSH.W
        hw2 = struct.unpack_from("<H", data, off+2)[0]
        if hw2 & 0x4000:  # LR bit set
            prologues.append(off)

print(f"Total function prologues found: {len(prologues)}")
# Distribution
from collections import Counter
regions = Counter()
for p in prologues:
    regions[p >> 16] += 1
for region in sorted(regions.keys()):
    print(f"  0x{region:02X}0000: {regions[region]} functions")

# Now: look at a valid code function and see how it references data
# Let's pick the function at 0x183C0 and trace its LDR [PC, #imm] loads
print("\n=== LDR PC-RELATIVE LOADS in function at 0x183C0 ===")
md.detail = True
instrs = list(md.disasm(data[0x183C0:0x183C0+1024], 0x183C0))
for ins in instrs:
    if ins.mnemonic in ("ldr", "ldr.w"):
        for op in ins.operands:
            if op.type == 4 and op.mem.base == 15:
                pool_addr = ((ins.address + 4) & ~3) + op.mem.disp
                if pool_addr < len(data) - 3:
                    val = struct.unpack_from("<I", data, pool_addr)[0]
                    print(f"  0x{ins.address:06X}: ldr -> [0x{pool_addr:06X}] = 0x{val:08X}")

# Now look at a region with MANY functions and try to find CMP #0x8E
# Focus on regions 0x01xxxx and 0x06xxxx which have the most functions
print("\n=== SEARCHING ALL CMP VALUES IN CODE REGIONS ===")
cmp_values = Counter()
for chunk_start in range(0, 0x0A0000, 0x10000):
    chunk = data[chunk_start:chunk_start+0x10000]
    for ins in md.disasm(chunk, chunk_start):
        if ins.mnemonic in ("cmp", "cmp.w"):
            for op in ins.operands:
                if op.type == 2:
                    cmp_values[op.imm] += 1

print("Most common CMP immediate values:")
for val, count in cmp_values.most_common(30):
    name = ""
    if val == 0x8E: name = " [START_FIRMWARE_LOAD]"
    elif val == 0x8F: name = " [LOAD_FW_DATA]"
    elif val == 0x90: name = " [PROCESS_FIRMWARE_IMAGE]"
    elif val == 0x91: name = " [VERIFY_FW_IMAGE]"
    print(f"  CMP #0x{val:X} ({val}): {count} times{name}")

# Check specifically for 0x8E-0x91
print("\nSpecific FW command values:")
for v in [0x8E, 0x8F, 0x90, 0x91]:
    print(f"  CMP #0x{v:X}: {cmp_values.get(v, 0)} times")

