#!/usr/bin/env python3
"""Phase 2: Find firmware update validation by tracing PC-relative string loads."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

def load_binary():
    with open(BIN_PATH, "rb") as f:
        return f.read()

def disasm_range(data, start, length):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    return list(md.disasm(data[start:start+length], start))

def print_insns(instrs, highlights=None):
    if highlights is None:
        highlights = set()
    for i in instrs:
        mark = " <<<" if i.address in highlights else ""
        print(f"  0x{i.address:06X}: {i.bytes.hex():12s} {i.mnemonic:10s} {i.op_str}{mark}")

def find_ldr_to_string(data, string_addr):
    """Find LDR instructions whose literal pool value points to string_addr.
    For Thumb LDR Rt, [PC, #imm], the loaded value comes from a literal pool."""
    hits = []
    # First find all places where string_addr appears as a 32-bit value (literal pool entries)
    target_bytes = struct.pack("<I", string_addr)
    pos = 0
    while True:
        pos = data.find(target_bytes, pos)
        if pos == -1:
            break
        pool_addr = pos
        # Now find LDR instructions that reference this pool entry
        # Thumb LDR Rt,[PC,#imm]: range is PC+4 aligned down, up to +1020
        # So the LDR must be within ~1020 bytes before the pool entry
        search_start = max(0, pool_addr - 1024)
        search_len = pool_addr - search_start + 4
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        md.detail = True
        for i in md.disasm(data[search_start:search_start+search_len], search_start):
            if i.mnemonic in ("ldr", "ldr.w") and "pc" in i.op_str.lower():
                # Check if this LDR references our pool address
                # For Thumb-2: LDR Rt, [PC, #imm] loads from (PC & ~3) + 4 + imm
                # The detail should give us the memory operand
                for op in i.operands:
                    if op.type == 4:  # MEM
                        base = op.mem.base  # should be PC (15)
                        if base == 15:  # PC
                            disp = op.mem.disp
                            effective = ((i.address + 4) & ~3) + disp
                            if effective == pool_addr:
                                hits.append(i.address)
            # Also check adr/add instructions
            if i.mnemonic in ("adr", "adr.w"):
                for op in i.operands:
                    if op.type == 2:  # IMM
                        effective = ((i.address + 4) & ~3) + op.imm
                        if effective == pool_addr:
                            hits.append(i.address)
        pos += 1
    return hits

def find_function_bounds(data, addr, max_back=4096):
    """Find PUSH prologue backward, then find end (POP {pc} or BX LR)."""
    # Search backward for push
    func_start = None
    for off in range(2, max_back, 2):
        p = addr - off
        if p < 0:
            break
        hw = struct.unpack_from("<H", data, p)[0]
        # 16-bit PUSH with LR
        if (hw & 0xFF00) == 0xB500:
            func_start = p
            break
        # 32-bit PUSH (STMDB SP!)
        if hw == 0xE92D and p + 2 < len(data):
            func_start = p
            break
    return func_start

data = load_binary()
print(f"Loaded {len(data)} bytes\n")

# Key strings and their offsets
KEY_STRINGS = {
    0x0AC749: "Update image CRC valid: %s",
    0x0B4AE5: "CRC of update image passed",
    0x0B4B0C: "CRC of update image failed",
    0x0B4842: "Firmware Load (0x8E)",
    0x0B38C6: "Verify FW image chunk offset 0x0 %s",
    0x0B3938: "VERIFY_FW_IMAGE unsupported revision: %u",
    0x0B4ACB: "Image (0x90).",
    0x0B49E0: "Firmware image load succeeded",
    0x0B4A40: "Firmware load command failed. Update flash not init.",
    0x0B5A6F: "Verify FW image chunk offset 0x%x %s",
    0x0B616A: "header Valid, Magic 0x%08lx version unrecognized",
    0x0B5F25: "crc32 for flash header. Code %d",
    0x0B5F5A: "crc32 mismatch. Expected: 0x%x Detected: 0x%x",
    0x0B5E5F: "valid header crc Pkt: %04x Calc: %04x",
}

all_code_refs = {}

for str_addr, str_text in KEY_STRINGS.items():
    refs = find_ldr_to_string(data, str_addr)
    if refs:
        print(f"String 0x{str_addr:06X} \"{str_text[:50]}\"")
        print(f"  Referenced from code at: {', '.join(f'0x{r:06X}' for r in refs)}")
        for r in refs:
            all_code_refs[r] = str_text
    else:
        # Try with +1 offset (strings sometimes off by format prefix)
        pass

print(f"\nTotal unique code references found: {len(all_code_refs)}")

# Now disassemble each function
print("\n" + "=" * 70)
print("DISASSEMBLY OF FIRMWARE UPDATE FUNCTIONS")
print("=" * 70)

seen_funcs = set()
for code_addr in sorted(all_code_refs.keys()):
    func_start = find_function_bounds(data, code_addr)
    if func_start and func_start in seen_funcs:
        continue
    if func_start:
        seen_funcs.add(func_start)
    
    start = func_start if func_start else max(0, code_addr - 64)
    
    print(f"\n{'='*60}")
    print(f"Function at 0x{start:06X} (string ref: \"{all_code_refs[code_addr][:50]}\")")
    print(f"{'='*60}")
    
    # Disassemble up to 2KB or until we hit a clear end
    instrs = disasm_range(data, start, min(2048, len(data) - start))
    
    # Find all string refs in this function
    highlights = {a for a in all_code_refs if start <= a < start + 2048}
    
    # Trim: go until POP {pc} after the last highlight
    trimmed = []
    max_highlight = max(highlights) if highlights else code_addr
    found_end = False
    for ins in instrs:
        trimmed.append(ins)
        if ins.address > max_highlight + 200:
            if ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
                found_end = True
                break
            if ins.mnemonic == "bx" and "lr" in ins.op_str:
                found_end = True
                break
        if len(trimmed) > 400:
            break
    
    print_insns(trimmed, highlights)
    
    # Summarize interesting instructions
    print(f"\n  -- Key instructions in this function --")
    for ins in trimmed:
        # Show BL calls (function calls)
        if ins.mnemonic in ("bl", "blx"):
            print(f"     CALL 0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
        # Show CMP with interesting values
        if ins.mnemonic in ("cmp", "cmp.w"):
            print(f"     CMP  0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}")
        # Show string loads (LDR from literal pool we identified)
        if ins.address in highlights:
            print(f"     STR  0x{ins.address:06X}: loads \"{all_code_refs[ins.address][:60]}\"")

print("\n\nDone.")
