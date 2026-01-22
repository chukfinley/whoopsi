#!/usr/bin/env python3
"""Analyze Whoop Maverick firmware for update validation logic."""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
BASE_ADDR = 0x00000000  # Assume loaded at 0

# Known string offsets (from previous analysis)
STRINGS_OF_INTEREST = {
    0x0AC742: "Update image CRC valid: %s",
    0x0B4ADA: "CRC of update image passed",
}

# BLE command bytes for firmware update
FW_COMMANDS = {0x8E: "START_FIRMWARE_LOAD", 0x8F: "LOAD_FW_DATA", 0x90: "PROCESS_FIRMWARE_IMAGE", 0x91: "VERIFY_FW_IMAGE"}

def load_binary():
    with open(BIN_PATH, "rb") as f:
        return f.read()

def find_references(data, addr, width=4):
    """Find all locations where addr appears as a 32-bit LE value."""
    target = struct.pack("<I", addr)
    refs = []
    pos = 0
    while True:
        pos = data.find(target, pos)
        if pos == -1:
            break
        refs.append(pos)
        pos += 1
    return refs

def find_function_start(data, offset, max_back=2048):
    """Search backward for a PUSH instruction that looks like a function prologue."""
    # Thumb-2 PUSH with LR: 2de9 xx xx (32-bit) or b5xx (16-bit push with LR)
    # Search backward in 2-byte steps
    for i in range(2, max_back, 2):
        pos = offset - i
        if pos < 0:
            break
        hw = struct.unpack_from("<H", data, pos)[0]
        # 16-bit PUSH {... LR}: 0xB500 | regs, bit 8 = LR
        if (hw & 0xFF00) == 0xB500:
            return pos
        # 32-bit PUSH (stmdb sp!, {...}): first halfword 0xE92D
        if hw == 0xE92D and pos + 2 < len(data):
            return pos
    return None

def disassemble_region(data, start, length, base=0):
    """Disassemble a region as Thumb-2."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    region = data[start:start+length]
    instructions = list(md.disasm(region, base + start))
    return instructions

def print_disassembly(instrs, highlight_addrs=None):
    if highlight_addrs is None:
        highlight_addrs = set()
    for i in instrs:
        marker = " <<<" if i.address in highlight_addrs else ""
        print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}{marker}")

def find_nearby_cmp_values(instrs, values_of_interest):
    """Find CMP/CMN instructions with immediate values matching our set."""
    hits = []
    for i in instrs:
        if i.mnemonic in ("cmp", "cmn", "cmp.w", "tst", "movs", "mov", "mov.w", "ldrb", "sub", "subs"):
            for v in values_of_interest:
                if f"#0x{v:x}" in i.op_str or f"#{v}" in i.op_str:
                    hits.append((i.address, i.mnemonic, i.op_str, v))
    return hits

def scan_for_cmp_bytes_near_strings(data, string_refs, radius=512):
    """Around each string reference, disassemble and look for FW command comparisons."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    all_hits = []
    for ref in string_refs:
        start = max(0, ref - radius)
        end = min(len(data), ref + radius)
        region = data[start:end]
        for i in md.disasm(region, BASE_ADDR + start):
            for cmd_byte, cmd_name in FW_COMMANDS.items():
                op = i.op_str.lower()
                if f"#0x{cmd_byte:x}" in op or f"#{cmd_byte}" in op:
                    all_hits.append((i.address, i.mnemonic, i.op_str, cmd_byte, cmd_name))
    return all_hits

def main():
    data = load_binary()
    print(f"Loaded {len(data)} bytes from firmware binary\n")

    # =========================================================
    # 1. Find all references to CRC validation strings
    # =========================================================
    print("=" * 70)
    print("STEP 1: Finding references to CRC validation strings")
    print("=" * 70)

    all_string_ref_locs = []
    for str_addr, str_text in STRINGS_OF_INTEREST.items():
        # Verify string is there
        actual = data[str_addr:str_addr+20]
        print(f"\n  String at 0x{str_addr:06X}: {actual[:30]}")
        
        # The address used in code may have base offset. Try both raw and with
        # common load address offsets. For Ambiq Apollo4, code often at 0x00018000
        # or 0x00000000. Since vector table is at 0x200, base is likely 0.
        for base_try in [0x00000000, 0x00018000, 0x00060000, 0x00080000]:
            target_addr = str_addr + base_try
            refs = find_references(data, target_addr)
            if refs:
                print(f"  -> {len(refs)} references found (base=0x{base_try:06X}, searching for 0x{target_addr:08X}):")
                for r in refs:
                    print(f"     at offset 0x{r:06X}")
                    all_string_ref_locs.append(r)

    # Also try: the string might be referenced via PC-relative addressing.
    # Let's do a broader search - look for the string address bytes in any form
    print(f"\n  Total string reference locations found: {len(all_string_ref_locs)}")

    # =========================================================
    # 2. Disassemble functions around string references
    # =========================================================
    print("\n" + "=" * 70)
    print("STEP 2: Disassembling functions around string references")
    print("=" * 70)

    seen_funcs = set()
    for ref_loc in all_string_ref_locs:
        func_start = find_function_start(data, ref_loc)
        if func_start is None:
            print(f"\n  Could not find function prologue for ref at 0x{ref_loc:06X}")
            # Just disassemble around the reference
            start = max(0, ref_loc - 64)
            length = 256
        else:
            if func_start in seen_funcs:
                continue
            seen_funcs.add(func_start)
            start = func_start
            length = min(1024, len(data) - start)
            print(f"\n  Function at 0x{start:06X} (ref at 0x{ref_loc:06X}):")

        instrs = disassemble_region(data, start, length)
        # Trim at a reasonable endpoint (BX LR or POP with PC after the reference)
        trimmed = []
        past_ref = False
        for ins in instrs:
            trimmed.append(ins)
            if ins.address >= ref_loc:
                past_ref = True
            if past_ref and ins.mnemonic in ("bx", "pop", "pop.w") and "pc" in ins.op_str:
                break
            if len(trimmed) > 300:
                break

        print_disassembly(trimmed, highlight_addrs={ref_loc})

        # Check for FW command byte comparisons
        cmd_hits = find_nearby_cmp_values(trimmed, FW_COMMANDS.keys())
        if cmd_hits:
            print(f"\n  ** FW command comparisons found in this function:")
            for addr, mnem, ops, val in cmd_hits:
                print(f"     0x{addr:08X}: {mnem} {ops}  -> {FW_COMMANDS[val]}")

    # =========================================================
    # 3. Broader scan for FW command bytes near CRC strings
    # =========================================================
    print("\n" + "=" * 70)
    print("STEP 3: Scanning for FW command bytes (0x8E-0x91) near CRC strings")
    print("=" * 70)

    all_refs_for_scan = list(STRINGS_OF_INTEREST.keys()) + all_string_ref_locs
    hits = scan_for_cmp_bytes_near_strings(data, all_refs_for_scan, radius=2048)
    if hits:
        for addr, mnem, ops, cmd_byte, cmd_name in hits:
            print(f"  0x{addr:08X}: {mnem:8s} {ops}  -> 0x{cmd_byte:02X} ({cmd_name})")
    else:
        print("  No direct FW command byte comparisons found near CRC strings.")
        # Broader search: scan entire binary for cmp #0x8e..0x91
        print("\n  Doing full binary scan for CMP with 0x8E-0x91...")
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        md.detail = True
        # Scan in chunks
        CHUNK = 0x40000
        for chunk_start in range(0, len(data), CHUNK):
            chunk_end = min(chunk_start + CHUNK, len(data))
            region = data[chunk_start:chunk_end]
            for i in md.disasm(region, BASE_ADDR + chunk_start):
                if i.mnemonic in ("cmp", "cmp.w"):
                    for cmd_byte, cmd_name in FW_COMMANDS.items():
                        if f"#0x{cmd_byte:x}" in i.op_str:
                            print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}  -> {cmd_name}")

    # =========================================================
    # 4. Search for Ambiq OTA magic / zbin header parsing
    # =========================================================
    print("\n" + "=" * 70)
    print("STEP 4: Searching for Ambiq OTA / zbin header parsing")
    print("=" * 70)

    # Ambiq OTA image magic: 0x4D415249 ("MRAI" = "IRAM" LE) or similar
    # Common Ambiq magic bytes
    AMBIQ_MAGICS = {
        b"\x4D\x41\x52\x49": "IRAM magic (Ambiq)",
        b"\x41\x4D\x00\x00": "AM magic",
        b"\x12\x34\x56\x78": "Generic test magic",
    }
    
    for magic, desc in AMBIQ_MAGICS.items():
        pos = 0
        while True:
            pos = data.find(magic, pos)
            if pos == -1:
                break
            print(f"  Magic '{desc}' found at offset 0x{pos:06X}")
            pos += 1

    # Look for offset 0x200 as an immediate value (vector table / header size)
    print("\n  Searching for references to 0x200 (header/vector table offset)...")
    # In Thumb-2, mov r?, #0x200 or cmp r?, #0x200 or ldr with 0x200
    # 0x200 = 512
    # mov.w Rd, #0x200 -> f44f 7d00 (rough pattern)
    # Let's just disassemble and look
    count_200 = 0
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    CHUNK = 0x40000
    for chunk_start in range(0, len(data), CHUNK):
        chunk_end = min(chunk_start + CHUNK, len(data))
        region = data[chunk_start:chunk_end]
        for i in md.disasm(region, BASE_ADDR + chunk_start):
            if "#0x200" in i.op_str and i.mnemonic in ("mov", "mov.w", "movs", "cmp", "cmp.w", "add", "add.w", "sub", "sub.w", "ldr", "str"):
                # Filter: only show if near other interesting code
                count_200 += 1
                if count_200 <= 30:
                    print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")
    if count_200 > 30:
        print(f"  ... and {count_200 - 30} more references to 0x200")

    # =========================================================
    # 5. Look for CRC calculation functions near the CRC16 table
    # =========================================================
    print("\n" + "=" * 70)
    print("STEP 5: Functions near CRC16 table at 0x0AC3B4")
    print("=" * 70)

    # Find references to the CRC16 table address
    crc_table_addr = 0x0AC3B4
    for base_try in [0x00000000]:
        target = crc_table_addr + base_try
        refs = find_references(data, target)
        if refs:
            print(f"  References to CRC16 table (0x{target:08X}):")
            for r in refs:
                print(f"    at offset 0x{r:06X}")
                # Disassemble around each reference
                func_start = find_function_start(data, r)
                if func_start:
                    print(f"    Function prologue at 0x{func_start:06X}:")
                    instrs = disassemble_region(data, func_start, min(512, len(data)-func_start))
                    # Trim to end of function
                    trimmed = []
                    past_ref = False
                    for ins in instrs:
                        trimmed.append(ins)
                        if ins.address >= r:
                            past_ref = True
                        if past_ref and ins.mnemonic in ("bx", "pop", "pop.w") and ("lr" in ins.op_str or "pc" in ins.op_str):
                            break
                        if len(trimmed) > 150:
                            break
                    print_disassembly(trimmed)
                    print()

    # =========================================================
    # 6. Search for "verify", "valid", "update", "firmware" strings
    # =========================================================
    print("\n" + "=" * 70)
    print("STEP 6: Additional firmware update related strings")
    print("=" * 70)

    search_terms = [b"verify", b"Verify", b"VERIFY", b"firmware", b"Firmware", 
                    b"update", b"Update", b"image", b"Image", b"crc", b"CRC",
                    b"signature", b"Signature", b"hash", b"Hash", b"SHA", b"sha",
                    b"valid", b"Valid", b"header", b"Header", b"zbin", b".bin",
                    b"OTA", b"ota", b"bootload", b"Bootload"]

    found_strings = {}
    for term in search_terms:
        pos = 0
        while True:
            pos = data.find(term, pos)
            if pos == -1:
                break
            # Extract full null-terminated string
            end = data.find(b"\x00", pos)
            if end == -1 or end - pos > 200:
                end = pos + 80
            s = data[pos:end]
            try:
                decoded = s.decode("ascii", errors="replace")
                # Filter out garbage
                if len(decoded) > 4 and all(c.isprintable() or c == '\r' or c == '\n' for c in decoded[:20]):
                    if pos not in found_strings:
                        found_strings[pos] = decoded
            except:
                pass
            pos += 1

    for addr in sorted(found_strings.keys()):
        s = found_strings[addr][:80]
        print(f"  0x{addr:06X}: {s}")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
