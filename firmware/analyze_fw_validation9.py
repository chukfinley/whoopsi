#!/usr/bin/env python3
"""Phase 9: Verify code is decodable. Check if binary has sections that are 
encrypted/compressed. Map out the binary layout."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

# Check the NMI handler at 0xDF964 (0xDF965 & ~1)
print("NMI Handler at 0x0DF964:")
for i, ins in enumerate(md.disasm(data[0xDF964:0xDF964+32], 0xDF964)):
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
    if i > 8: break

# Check reset handler at 0x4A4D8
print("\nReset Handler at 0x4A4D8:")
for i, ins in enumerate(md.disasm(data[0x4A4D8:0x4A4D8+64], 0x4A4D8)):
    print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
    if i > 15: break

# Check SVC handler (vector 11) at offset 0x22C -> address 
svc = struct.unpack_from("<I", data, 0x200 + 11*4)[0]
print(f"\nSVC Handler vector: 0x{svc:08X}")
svc_addr = svc & ~1
if svc_addr < len(data):
    print(f"SVC Handler at 0x{svc_addr:06X}:")
    for i, ins in enumerate(md.disasm(data[svc_addr:svc_addr+32], svc_addr)):
        print(f"  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
        if i > 8: break

# Map entropy/byte distribution across the binary
print("\n\nBinary region analysis (entropy indicator):")
print(f"{'Offset':>10} {'Zeros%':>7} {'0xFF%':>7} {'ASCII%':>7} {'Unique':>7} {'Type':>15}")

for off in range(0, len(data), 0x10000):
    chunk = data[off:off+0x10000]
    n = len(chunk)
    zeros = chunk.count(0)
    ffs = chunk.count(0xFF)
    ascii_count = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
    unique = len(set(chunk))
    
    if ascii_count / n > 0.5:
        typ = "STRINGS/DATA"
    elif zeros / n > 0.3:
        typ = "SPARSE/CODE"
    elif unique < 100:
        typ = "REPETITIVE"
    elif ffs / n > 0.3:
        typ = "PADDING/EMPTY"
    else:
        typ = "CODE/DATA"
    
    print(f"  0x{off:06X}  {zeros/n*100:6.1f}%  {ffs/n*100:6.1f}%  {ascii_count/n*100:6.1f}%  {unique:6d}  {typ}")

# Check if the header at 0x0 describes sections
print("\n\nImage header analysis (first 0x200 bytes):")
print("First 64 bytes as words:")
for i in range(0, min(0x100, len(data)), 4):
    val = struct.unpack_from("<I", data, i)[0]
    # Check if any look like string data
    b = data[i:i+4]
    try:
        s = b.decode('ascii')
        if all(c.isprintable() for c in s):
            print(f"  0x{i:03X}: 0x{val:08X}  '{s}'")
            continue
    except:
        pass
    print(f"  0x{i:03X}: 0x{val:08X}")

# The header at offset 0x18 seems to have ASCII. Let's decode it.
print("\nHeader string at 0x18:")
end = data.index(0, 0x18)
print(f"  {data[0x18:end].decode('ascii', errors='replace')}")

