#!/usr/bin/env python3
"""Examine the second table (91 entries) as potential handler for commands 0x77+.
If base=0x77, then 0x8E=idx 23, 0x8F=idx 24, 0x90=idx 25, 0x91=idx 26."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

def disasm(start, length=2048):
    return list(md.disasm(data[start:start+length], start))

def print_insns(instrs, n=120):
    for i, ins in enumerate(instrs[:n]):
        print(f"  0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}")

# Table 2 at 0x0442BC, 91 entries
# If first table covers 0x00-0x76 (119 entries), second covers 0x77-0xD1 (91 entries)
# 0x8E - 0x77 = 0x17 = 23
# 0x8F - 0x77 = 0x18 = 24  
# 0x90 - 0x77 = 0x19 = 25
# 0x91 - 0x77 = 0x1A = 26

table2 = 0x0442BC

# Check indices 22-27 for firmware update handlers
FW_INDICES = {
    23: "START_FIRMWARE_LOAD (0x8E)?",
    24: "LOAD_FW_DATA (0x8F)?",
    25: "PROCESS_FIRMWARE_IMAGE (0x90)?",
    26: "VERIFY_FW_IMAGE (0x91)?",
    22: "cmd 0x8D (before FW)?",
    27: "SET_CLOCK (0x92)?",
}

for idx, name in sorted(FW_INDICES.items()):
    off = table2 + idx * 4
    val = struct.unpack_from("<I", data, off)[0]
    target = val & ~1
    print(f"\n{'='*60}")
    print(f"Table2[{idx}/0x{idx:02X}] = 0x{target:06X}  [{name}]")
    print(f"{'='*60}")
    instrs = disasm(target, 1024)
    # Find the end of this function
    trimmed = []
    for ins in instrs:
        trimmed.append(ins)
        if len(trimmed) > 5 and ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
            break
        if ins.mnemonic == "bx" and "lr" in ins.op_str:
            break
        if len(trimmed) > 120:
            break
    print_insns(trimmed)
    
    # Show BL calls
    print("  -- Calls:")
    for ins in trimmed:
        if ins.mnemonic in ("bl", "blx"):
            target_addr = None
            if ins.op_str.startswith("#"):
                target_addr = int(ins.op_str[1:], 0)
            elif ins.op_str.startswith("0x"):
                target_addr = int(ins.op_str, 0)
            if target_addr:
                print(f"     bl 0x{target_addr:06X}")

# Also: the default handler 0x08D048 - let's see what it does (probably returns error)
print(f"\n{'='*60}")
print(f"DEFAULT HANDLER at 0x08D048")
print(f"{'='*60}")
instrs = disasm(0x08D048, 64)
print_insns(instrs, 20)

# Check: does the table actually start at 0x04405C or earlier?
# Look for the code that indexes into these tables
# The tables should be referenced by LDR Rn, =table_addr
# Let me search for 0x04405C and 0x0442BC in literal pools
for tbl, name in [(0x04405C, "Table1"), (0x0442BC, "Table2")]:
    target = struct.pack("<I", tbl)
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1: break
        print(f"\n{name} (0x{tbl:06X}) in literal pool at 0x{pos:06X}")
        # Find the LDR that reads this
        for back in range(4, 1028, 2):
            test_addr = pos - back
            if test_addr < 0: break
            for ins in md.disasm(data[test_addr:test_addr+4], test_addr):
                if ins.mnemonic in ("ldr", "ldr.w") and "pc" in ins.op_str:
                    pool = ((ins.address + 4) & ~3) + int(ins.op_str.split("#")[1].rstrip("]"), 0)
                    if pool == pos:
                        print(f"  LDR at 0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
                        # Show surrounding code
                        fs = None
                        for fb in range(2, 2048, 2):
                            p = ins.address - fb
                            hw = struct.unpack_from("<H", data, p)[0]
                            if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
                                fs = p
                                break
                        if fs:
                            print(f"  Function at 0x{fs:06X}:")
                            finstrs = disasm(fs, min(1024, ins.address - fs + 128))
                            print_insns(finstrs, 80)
                break
        pos += 1

