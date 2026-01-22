#!/usr/bin/env python3
"""Phase 11: Find the REAL code. The vector table at 0x200 may be encrypted 
or this may not be the right vector table. Let's:
1. Search for actual PUSH {r4-r7,lr} prologues to find real code regions
2. Find a valid reset handler pattern (typical: LDR SP, disable interrupts, etc.)
3. Try different interpretations of the binary format
"""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

# Count PUSH instructions per 64KB block to find code regions
print("PUSH instruction density by 64KB block:")
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

for block in range(0, len(data), 0x10000):
    chunk = data[block:block+0x10000]
    push_count = 0
    for ins in md.disasm(chunk, block):
        if ins.mnemonic in ("push", "push.w"):
            push_count += 1
    if push_count > 0:
        print(f"  0x{block:06X}: {push_count} PUSH instructions")

# Check: is offset 0x200 really a vector table? Look at alignment and typical patterns.
# A real ARM vector table has SP at [0], reset at [1], then exception handlers.
# SP should be in SRAM (0x10000000 range for Apollo4).
# The value at 0x200 is 0x10009C40 - looks like valid SRAM.
# Reset vector 0x4A4D9 - reasonable flash address.
# But the code there doesn't decode properly...

# Maybe the binary itself isn't loaded at offset 0 in the file.
# The Ambiq .bin format: 0x200 byte header, then code.
# If the code section starts at offset 0x200 and is loaded at 0x00018000,
# then vector table is at memory 0x18000, and reset vector 0x4A4D9 maps to
# file offset 0x4A4D9 - 0x18000 + 0x200 = 0x324D9 ... wait, that's 0x324D8+0x200 = 0x346D8?
# No. If header is 0x200 bytes, and code starts at file offset 0x200, loaded at 0x18000:
# mem_addr = file_offset - 0x200 + 0x18000
# So file_offset = mem_addr - 0x18000 + 0x200
# Reset 0x4A4D8: file = 0x4A4D8 - 0x18000 + 0x200 = 0x326D8

# Let's try this mapping
print("\n\nTrying mapping: code at file 0x200 loaded at 0x18000")
print("mem = file - 0x200 + 0x18000, file = mem - 0x18000 + 0x200")
reset_file = 0x4A4D8 - 0x18000 + 0x200
nmi_file = 0xDF964 - 0x18000 + 0x200
print(f"Reset handler file offset: 0x{reset_file:06X}")
print(f"NMI handler file offset: 0x{nmi_file:06X}")

md2 = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md2.detail = True

if reset_file < len(data):
    print("\nReset handler:")
    for i, ins in enumerate(md2.disasm(data[reset_file:reset_file+64], 0x4A4D8)):
        print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
        if i > 15: break

if nmi_file < len(data):
    print("\nNMI handler:")
    for i, ins in enumerate(md2.disasm(data[nmi_file:nmi_file+32], 0xDF964)):
        print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
        if i > 8: break

# Also try: maybe the image starts at file offset 0 and is loaded at 0x18000
# (no header split). In that case vector table at file 0x200 = memory 0x18200.
# VTOR would be set to 0x18200.
# Reset vector 0x4A4D9 -> file offset 0x4A4D9 - 0x18000 = 0x324D9 & ~1 = 0x324D8
# This is what we already tried in phase 10. That was garbage too.

# Try: base = 0. Entire file at address 0. Vector table at 0x200.
# Reset = 0x4A4D8. Code at file 0x4A4D8 was garbage.
# But wait - we showed PUSH density above. Let's see where REAL code actually is.

print("\n\nSearching for typical reset handler patterns in code regions...")
# A reset handler typically starts with: LDR SP, ...; CPSID; or similar
# But at minimum it should be a valid function entry.
# Let's find the first few PUSH {lr} or PUSH {r4-r7,lr} in each region

for region_start in [0x200, 0x18200]:
    # Find first PUSH near start
    for off in range(region_start, min(region_start + 0x1000, len(data)), 2):
        hw = struct.unpack_from("<H", data, off)[0]
        if hw == 0xB500 or hw == 0xB510 or hw == 0xB530 or hw == 0xB570 or hw == 0xB5F0:
            print(f"\nFirst PUSH near 0x{region_start:06X}: at file 0x{off:06X} (hw=0x{hw:04X})")
            for i, ins in enumerate(md2.disasm(data[off:off+64], off)):
                print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
                if i > 15: break
            break

# The real test: find a BL target from the SVC handler that makes sense
# PendSV vector at [14] = 0x0004A541
# If base=0: file 0x4A540 -> we showed this was BL + valid code
# But the BL #0x3765c from 0x4A540: target = 0x4A540 + 4 + 0x3765c = 0x81BA0
# Is there valid code at 0x81BA0?
print("\nChecking BL target 0x81BA0 (from SVC handler at file 0x4A540):")
for i, ins in enumerate(md2.disasm(data[0x81BA0:0x81BA0+32], 0x81BA0)):
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
    if i > 8: break

# What about file offset interpretation: code at 0x4A540 BL to 0x3765C
# Target addr = 0x4A544 + 0x3765C = 0x81BA0. If base=0, file=0x81BA0. Check:
print("\nAlso checking if BL math is correct for base=0:")
# Actually capstone shows the absolute target for BL. So 0x3765c IS the absolute target?
# No, capstone shows the absolute resolved address for BL.
# Let me re-check
for ins in md2.disasm(data[0x4A540:0x4A540+8], 0x4A540):
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str} (bytes: {ins.bytes.hex()})")

# For BL, capstone resolves it to absolute address. So #0x3765c is the absolute target.
# Check code at 0x3765C
print("\nCode at absolute 0x3765C:")
for i, ins in enumerate(md2.disasm(data[0x3765C:0x3765C+32], 0x3765C)):
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
    if i > 8: break

