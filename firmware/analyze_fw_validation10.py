#!/usr/bin/env python3
"""Phase 10: Apollo4 Blue Plus has MRAM at 0x00018000. The 0x200-byte header 
is the Ambiq image header, and the vector table at offset 0x200 in the file 
maps to 0x00018000 + 0x200 = 0x00018200 in memory. BUT the vectors themselves 
contain addresses like 0x4A4D9 which don't include that base. 

Actually for Apollo4, the MRAM starts at 0x00018000, but images can be loaded 
at the start of MRAM. The vector table offset tells NVIC where vectors are.

Let me reconsider: the header says size 0x00179ED8 at offset 4. The file is 
1,548,504 = 0x17A1D8 bytes. So 0x17A1D8 - 0x200 (header) = 0x179FD8 != 0x179ED8.
Close but not exact (off by 0x100).

The real question: what's the actual load address? The vectors point to low 
addresses like 0x4A4D9. For Apollo4 Blue Plus, MRAM is at 0x00018000.
If the binary loads at 0x00018000, then file_offset = addr - 0x00018000.
Vector 0x4A4D9 -> file offset 0x4A4D9 - 0x18000 = 0x324D9.

Let me check if code at file offset 0x324D8 looks like a reset handler.
"""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

BASE = 0x00018000  # MRAM base for Apollo4

# Reset vector = 0x4A4D9, so code at 0x4A4D8
# File offset = 0x4A4D8 - 0x18000 = 0x324D8
reset_file_off = 0x4A4D8 - BASE
print(f"Reset handler: mem 0x{0x4A4D8:06X}, file offset 0x{reset_file_off:06X}")
instrs = list(md.disasm(data[reset_file_off:reset_file_off+64], 0x4A4D8))
for ins in instrs:
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# NMI at 0xDF965 -> 0xDF964, file offset 0xDF964 - 0x18000 = 0xC7964
nmi_off = 0xDF964 - BASE
print(f"\nNMI handler: mem 0x{0xDF964:06X}, file offset 0x{nmi_off:06X}")
if nmi_off < len(data):
    instrs = list(md.disasm(data[nmi_off:nmi_off+32], 0xDF964))
    for ins in instrs:
        print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")

# Now let's find string references. String "Update image CRC valid" is at 
# file offset 0x0AC749. Memory address = 0x0AC749 + 0x18000 = 0x0C4749
str_mem_addr = 0x0AC749 + BASE
print(f"\n'Update image CRC valid' at memory addr 0x{str_mem_addr:06X}")

# Search for this value in literal pools
target = struct.pack("<I", str_mem_addr)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    mem_addr = pos + BASE
    print(f"  Literal pool at file 0x{pos:06X} (mem 0x{mem_addr:06X})")
    # Find the function
    pos += 1

# Try all the key strings
KEY_STRINGS = {
    0x0AC742: "%6llu: Update image CRC valid: %s",
    0x0B4AE5: "CRC of update image passed",
    0x0B4B0C: "CRC of update image failed", 
    0x0B4842: "Firmware Load (0x8E)",
    0x0B38C6: "Verify FW image chunk offset 0x0 %s",
    0x0B3938: "VERIFY_FW_IMAGE unsupported revision: %u",
    0x0B4ACB: "Image (0x90).",
    0x0B49E0: "Firmware image load succeeded",
    0x0B5F5A: "crc32 mismatch",
    0x0B616A: "header Valid, Magic",
}

print("\nSearching for all key string addresses with base 0x18000:")
all_refs = {}
for file_off, text in KEY_STRINGS.items():
    mem_addr = file_off + BASE
    target = struct.pack("<I", mem_addr)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  0x{pos:06X} (mem 0x{pos+BASE:06X}): -> \"{text[:50]}\"")
        all_refs[pos] = text
        pos += 1

print(f"\nTotal refs found: {len(all_refs)}")

# Also search for CRC16 table with base offset
crc16_mem = 0x0AC3B4 + BASE
target = struct.pack("<I", crc16_mem)
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    print(f"\nCRC16 table ref at file 0x{pos:06X} (mem 0x{pos+BASE:06X})")
    pos += 1

# Search for CMP 0x8E-0x91 in proper code region
print("\n\nSearching for BLE command CMP in code region (file 0x000-0x0A0000, base-adjusted)...")
fw_cmds = {0x8E: "START_FIRMWARE_LOAD", 0x8F: "LOAD_FW_DATA", 
            0x90: "PROCESS_FIRMWARE_IMAGE", 0x91: "VERIFY_FW_IMAGE"}

# Code is from file 0x200 to ~0x0A0000 (adjusted)
# Scan for CMP Rn, #0x8E etc
CHUNK = 0x20000
for chunk_start in range(0x200, 0x0A0000, CHUNK):
    chunk_end = min(chunk_start + CHUNK, len(data))
    region = data[chunk_start:chunk_end]
    for ins in md.disasm(region, chunk_start + BASE):
        if ins.mnemonic in ("cmp", "cmp.w"):
            for cmd_val, cmd_name in fw_cmds.items():
                if f"#0x{cmd_val:x}" in ins.op_str:
                    file_off = ins.address - BASE
                    print(f"  0x{ins.address:06X} (file 0x{file_off:06X}): {ins.mnemonic} {ins.op_str}  [{cmd_name}]")

# Search for CRC32 polynomial with base-adjusted scan
print("\nSearching for CRC32 polynomials...")
for poly, name in [(0xEDB88320, "reflected"), (0x04C11DB7, "normal")]:
    target = struct.pack("<I", poly)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"  {name} at file 0x{pos:06X}")
        pos += 1

