#!/usr/bin/env python3
"""
Whoop Maverick Firmware Update Validation Analysis
===================================================
Firmware: maverick-50.35.2.0.bin (1,548,504 bytes)
Target: ARM Cortex-M4F (Thumb-2), Ambiq Apollo4 Blue Plus

Analyzes CRC validation, firmware update code paths, and image header parsing.
"""

import struct
from capstone import *

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")

with open(BIN_PATH, "rb") as f:
    data = f.read()

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

def disasm(start, length=512):
    return list(md.disasm(data[start:start+length], start))

def find_func_start(addr):
    for off in range(2, 8192, 2):
        p = addr - off
        if p < 0: return None
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
            return p
    return None

def find_bl_targets(start, length):
    """Find all BL instruction targets in a range."""
    targets = []
    for off in range(start, min(start+length, len(data)-3), 2):
        hw1 = struct.unpack_from("<H", data, off)[0]
        hw2 = struct.unpack_from("<H", data, off+2)[0]
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
            S = (hw1 >> 10) & 1
            imm10 = hw1 & 0x3FF
            J1 = (hw2 >> 13) & 1
            J2 = (hw2 >> 11) & 1
            imm11 = hw2 & 0x7FF
            I1 = 1 - (J1 ^ S)
            I2 = 1 - (J2 ^ S)
            imm32 = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
            if S: imm32 |= 0xFE000000
            target = (off + 4 + imm32) & 0xFFFFFFFF
            targets.append((off, target))
    return targets

def find_callers(func_addr):
    """Find all BL instructions targeting func_addr."""
    callers = []
    for off, target in find_bl_targets(0, len(data)):
        if target == func_addr or target == func_addr + 1:
            callers.append(off)
    return callers

def print_function(start, max_insns=100, annotations=None):
    if annotations is None: annotations = {}
    instrs = disasm(start, max_insns * 4)
    count = 0
    for ins in instrs:
        ann = annotations.get(ins.address, "")
        print(f"  0x{ins.address:06X}: {ins.bytes.hex():12s} {ins.mnemonic:10s} {ins.op_str}{ann}")
        count += 1
        if count > 5 and ins.mnemonic in ("pop","pop.w") and "pc" in ins.op_str:
            return ins.address
        if count >= max_insns:
            print(f"  ... (truncated at {max_insns} instructions)")
            return ins.address
    return start

print("=" * 78)
print("WHOOP MAVERICK FIRMWARE UPDATE VALIDATION ANALYSIS")
print("=" * 78)

# ============================================================
print("\n" + "=" * 78)
print("1. BINARY STRUCTURE")
print("=" * 78)

print(f"""
  File size:        {len(data)} bytes (0x{len(data):06X})
  Image header:     0x000000-0x0001FF (Ambiq OTA header, magic 0x6EB0D692)
  Vector table:     0x000200-0x0002FF
  
  Image header fields:
    Magic:          0x{struct.unpack_from('<I', data, 0)[0]:08X}
    Payload size:   0x{struct.unpack_from('<I', data, 4)[0]:08X} ({struct.unpack_from('<I', data, 4)[0]} bytes)
    Encryption:     0x{struct.unpack_from('<I', data, 8)[0]:08X} (type/algo)
    Auth algo:      0x{struct.unpack_from('<I', data, 0xC)[0]:08X}
    Auth key idx:   0x{struct.unpack_from('<I', data, 0x10)[0]:08X}
    Build hash:     {data[0x18:0x44].split(b'\\x00')[0].decode('ascii', errors='replace')}
    Version:        {data[0x4C:0x58].split(b'\\x00')[0].decode('ascii', errors='replace')}
    Board:          {data[0x64:0x74].split(b'\\x00')[0].decode('ascii', errors='replace')}
    
  Header CRC:       0x{struct.unpack_from('<I', data, 0x1F8)[0]:08X} (at offset 0x1F8)
  Second magic:     0x{struct.unpack_from('<I', data, 0x1FC)[0]:08X} (at offset 0x1FC)

  Memory regions:
    0x000000-0x09FFFF: Code (~640KB, {sum(1 for p in range(0, 0xA0000, 2) if struct.unpack_from('<H', data, p)[0] & 0xFF00 == 0xB500)} PUSH prologues)
    0x0A0000-0x0CFFFF: Strings/Data (high ASCII density)
    0x0D0000-0x0FFFFF: Mixed code/data (high entropy)
    0x100000-0x16FFFF: Additional code + data tables
    0x170000-0x17A0D8: Sparse data / padding
""")

# ============================================================
print("=" * 78)
print("2. CRC FUNCTIONS IDENTIFIED")
print("=" * 78)

print("""
  CRC16 (reflected, init=0xFFFF):
    Table:       0x0AC3B4 (256 x 16-bit, CRC-16 reflected polynomial)
    Function:    0x0127D8 (core loop: XOR byte, table lookup, shift)
    Entry point: 0x0127FC (sets init=0xFFFF, falls through to 0x0127D8)
    
  CRC32 (hardware-assisted via MSPI DMA):
    Function:    0x012804 (uses Ambiq MSPI peripheral for CRC32)
    Verify:      0x0127A0 (wrapper, calls CRC32 with verify flag)
""")

print("\n  CRC16 Core Function (0x0127D8):")
instrs = disasm(0x0127D8, 64)
for ins in instrs:
    print(f"    0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}")
    if ins.address >= 0x0127F6:
        break

print(f"""
    Algorithm: CRC-16 with reflected table
    - Loads table base from literal pool -> 0x0AC3B4
    - Loop: byte = *data++; idx = (byte ^ crc) & 0xFF; crc = table[idx] ^ (crc >> 8)
    - Initial value: 0xFFFF (set by wrapper at 0x0127FC)
""")

# ============================================================
print("=" * 78)
print("3. CRC VALIDATION CALL GRAPH")
print("=" * 78)

# CRC16 callers
crc16_callers = [
    (0x012926, 0x0128D4, "Packet frame builder - CRC16 for frame header"),
    (0x012B2A, 0x012AD4, "CRC16 verify from hex-encoded string"),
    (0x012BF8, 0x012B78, "Packet CRC16 verification"),
    (0x0301B4, 0x03010C, "Frame header CRC16 validation (0xAA marker check)"),
    (0x0345BE, 0x0344EC, "Data frame builder with CRC16 + CRC32 verify"),
    (0x034B48, 0x034A14, "Multi-frame data parser with CRC validation"),
    (0x049558, 0x04953A, "Response frame CRC16 computation"),
    (0x049630, 0x0495EC, "Frame builder type 0xAA with CRC16+CRC32"),
    (0x04969C, 0x049658, "Frame builder type 0xAA variant with CRC16+CRC32"),
]

print("\n  Direct CRC16 callers (0x0127FC):")
for call_addr, func_addr, desc in crc16_callers:
    print(f"    0x{call_addr:06X} in func 0x{func_addr:06X}: {desc}")

# Key frame validation function at 0x03010C
print("\n  Key function: Frame Header CRC16 Validation (0x03010C):")
print("  This validates incoming BLE data frames:")
instrs = disasm(0x03010C, 512)
annotations = {
    0x03011E: "  <- Check frame marker byte == 0xAA",
    0x0301B4: "  <- CRC16 of 6-byte header",
    0x0301BC: "  <- Compare computed CRC vs stored CRC",
    0x0301C0: "  <- Branch if CRC matches",
    0x030206: "  <- Check payload length field",
    0x03020A: "  <- Max payload 0xFB8 (4024) bytes",
}
count = 0
for ins in instrs:
    ann = annotations.get(ins.address, "")
    if ins.address in annotations or ins.mnemonic in ("cmp", "bl", "beq", "bne", "bls", "bhi") or count < 5:
        print(f"    0x{ins.address:06X}: {ins.mnemonic:10s} {ins.op_str}{ann}")
    count += 1
    if count > 3 and ins.mnemonic in ("pop", "pop.w") and "pc" in ins.op_str:
        break

# CRC32 callers and 2nd level
print("\n\n  CRC32 hardware callers (0x012804):")
print("    0x01274C, 0x01277C (in setup/init functions)")

# 2nd level callers for the frame builder functions
print("\n  Second-level callers (functions calling CRC-using functions):")
level2 = [
    (0x0128D4, "Frame builder", [(0x01299C,), (0x012A10,), (0x012A82,)]),
    (0x03010C, "Frame header validator", [(0x030D04,), (0x03187A,), (0x031A58,)]),
    (0x0344EC, "Data frame builder", [(0x0346E2,), (0x0347EA,)]),
    (0x034A14, "Multi-frame parser", [(0x0336E8,)]),
    (0x0495EC, "Response frame builder", [(0x024964,), (0x024D08,), (0x025B9A,), (0x049746,)]),
]
for func_addr, desc, callers in level2:
    caller_strs = ", ".join(f"0x{c[0]:06X}" for c in callers)
    print(f"    0x{func_addr:06X} ({desc}) <- {caller_strs}")

# ============================================================
print("\n" + "=" * 78)
print("4. FIRMWARE UPDATE STRING EVIDENCE")
print("=" * 78)

fw_strings = [
    (0x0B4842, "Firmware Load (0x8E)"),
    (0x0B48C8, "firmware load command processing"),
    (0x0B4959, "Firmware data length %u is too large. Cmd fail!"),
    (0x0B49E0, "Firmware image load succeeded"),
    (0x0B4A0A, "Firmware image load failed, error code %d"),
    (0x0B4A40, "Firmware load command failed. Update flash not init."),
    (0x0B4ACB, "Image (0x90)."),
    (0x0B4AE5, "CRC of update image passed"),
    (0x0B4B0C, "CRC of update image failed"),
    (0x0B4B42, "update flash on process image cmd."),
    (0x0B4A9A, "valid revision %d"),
    (0x0B4B94, "valid revision %d"),
    (0x0B38C6, "Verify FW image chunk offset 0x0 %s"),
    (0x0B3905, "update flash to verify image"),
    (0x0B3938, "VERIFY_FW_IMAGE unsupported revision: %u"),
    (0x0B5A6F, "Verify FW image chunk offset 0x%x %s"),
    (0x0B5303, "Update activity timeout! Powering down update flash"),
    (0x0B5354, "update flash on FW update timeout %d"),
]

print("\n  Firmware update log strings (in data section 0x0B0000-0x0B6000):")
for addr, text in fw_strings:
    print(f"    0x{addr:06X}: \"{text}\"")

print("""
  NOTE: These strings are in a SEPARATE data section and are NOT referenced
  by direct pointers from code. They appear to be part of a structured logging
  system (Memfault SDK detected at 0x0A9E29). The logging framework likely 
  uses string IDs or compile-time indexing rather than runtime pointers.
""")

# ============================================================
print("=" * 78)
print("5. IMAGE HEADER AND VALIDATION STRUCTURE")
print("=" * 78)

print("""
  Ambiq OTA Image Header (0x000-0x1FF):
  
  Offset  Size  Field                Value
  ------  ----  -----                -----
  0x000   4     Magic                0x6EB0D692
  0x004   4     Payload size         0x00179ED8 (1,547,992 bytes)
  0x008   4     Encryption type      0x00000005
  0x00C   4     Auth algorithm       0x00000001
  0x010   4     Auth key index       0x0000000D
  0x014   4     Reserved             0x00000000
  0x018   44    Build hash/info      #J\\x0Ai54fc551ae08a204f9d30ab17
  0x044   8     Timestamp            2025-11-04T18:46:59
  0x04C   12    Version              50.35.x.x
  0x064   16    Board                SEXTON2020P1
  0x07C   4     Unknown              0x00000032 (50)
  0x080   4     Unknown              0x00000023 (35)
  0x084   4     Unknown              0x00000002 (2)
  0x088-1F4     Zero padding
  0x1F8   4     Header CRC/checksum  0xB8CAF727
  0x1FC   4     Magic (repeated)     0x6EB0D692
  
  The header contains:
  - Image magic (0x6EB0D692) at both start and end of header
  - Encryption type 5 and auth algo 1 (suggesting signed+encrypted support)
  - Auth key index 0x0D (key 13 in a key table)
  - A CRC/checksum at 0x1F8 (0xB8CAF727) covering the header
  
  Frame Protocol Structure (from CRC16 validation code at 0x03010C):
  
  Byte 0:    Marker (0xAA for data frames)
  Byte 1:    Frame type/revision
  Bytes 2-3: Payload length (LE, max 0xFB8 = 4024)
  Bytes 4-5: Reserved
  Bytes 6-7: CRC16 of bytes 0-5
  Bytes 8+:  Payload data
  Last 4:    CRC32 of payload
""")

# ============================================================
print("=" * 78)
print("6. FIRMWARE UPDATE VALIDATION FLOW (RECONSTRUCTED)")
print("=" * 78)

print("""
  Based on strings and code analysis, the firmware update process is:

  1. START_FIRMWARE_LOAD (0x8E):
     - Initialize external SPI NOR flash for update storage
     - Flash types detected: ISSI 64Mb NOR, Winbond 64Mb NOR
     - String evidence: "Firmware Load (0x8E)", "Update Flash: ISSI 64Mb NOR detected"
     
  2. LOAD_FW_DATA (0x8F):
     - Receive firmware data chunks over BLE
     - Each chunk is wrapped in the CRC16+CRC32 frame protocol
     - Frame validation: CRC16 on 6-byte header, CRC32 on payload
     - Data written to external flash
     - String evidence: "Firmware data length %u is too large. Cmd fail!"
     
  3. PROCESS_FIRMWARE_IMAGE (0x90):
     - After all data received, validate the complete image
     - CRC of entire update image is checked
     - String evidence: "CRC of update image passed", "CRC of update image failed"
     - String: "Image (0x90)."
     
  4. VERIFY_FW_IMAGE (0x91):
     - Read-back verification of written firmware
     - Chunk-by-chunk verification at offsets
     - String evidence: "Verify FW image chunk offset 0x%x %s"
     - String: "VERIFY_FW_IMAGE unsupported revision: %u"
     
  5. Activity Timeout:
     - If update stalls, flash is powered down
     - String: "Update activity timeout! Powering down update flash"

  VALIDATION MECHANISMS IDENTIFIED:
  
  a) Transport-level: CRC16 (reflected, init=0xFFFF) on frame headers
     - Function: 0x0127D8/0x0127FC
     - Table: 0x0AC3B4 (256-entry CRC-16 reflected)
     - Used for every BLE data frame header
     
  b) Transport-level: CRC32 (hardware via MSPI DMA) on frame payloads
     - Function: 0x012804
     - Used for every BLE data frame payload
     
  c) Image-level: CRC of complete firmware image after transfer
     - Validated during PROCESS_FIRMWARE_IMAGE (0x90) command
     - "CRC of update image passed/failed"
     
  d) Readback verification during VERIFY_FW_IMAGE (0x91)
     - Chunk-by-chunk comparison
     
  e) Image header validation:
     - Magic 0x6EB0D692 checked
     - Header CRC at offset 0x1F8
     - "header Valid, Magic 0x%08lx version unrecognized"
     
  SECURITY ASSESSMENT:
  
  - CRC-only validation: The strings strongly suggest CRC is the PRIMARY
    validation mechanism for firmware images (no "signature verification 
    failed" or "RSA/ECDSA" strings related to FW update)
  - The auth_algo=1 and auth_key_idx=0xD in the header COULD indicate
    signature support, but there's NO code evidence of signature verification
    during the update process
  - NO CRC32 polynomial found as immediate constant (hardware CRC via MSPI)
  - NO RSA/ECDSA/Ed25519 key material or signature verification code identified
    in relation to firmware updates
  - The "signature" strings found (0x0BF584, 0x0BF722) relate to strap/device
    signatures, NOT firmware image authentication
""")

# ============================================================
print("=" * 78)
print("7. KEY ADDRESSES SUMMARY")
print("=" * 78)

print("""
  FUNCTIONS:
    0x0127D8  CRC16 core computation (reflected, table-based)
    0x0127FC  CRC16 with init=0xFFFF (main entry point)
    0x0127A0  CRC32 verify wrapper
    0x012804  CRC32 via hardware MSPI DMA
    0x03010C  BLE frame header validation (marker + CRC16 + length check)
    0x0344EC  Data frame builder with CRC16 header + CRC32 payload
    0x0495EC  Response frame builder (type 0xAA, CRC16+CRC32)
    0x049658  Response frame builder variant

  DATA:
    0x0AC3B4  CRC-16 reflected lookup table (256 x 16-bit entries)
    0x000000  Ambiq OTA image header (magic 0x6EB0D692)
    0x0001F8  Header CRC/checksum (0xB8CAF727)
    0x0001FC  Header magic repeated
    0x000200  ARM vector table (SP=0x10009C40, Reset=0x0004A4D9)

  PROTOCOL:
    Frame marker: 0xAA (data) / 0x55 (other type, ~0xAA)
    Max payload:  4024 bytes (0xFB8)
    Header CRC:   CRC-16 reflected, init 0xFFFF, over 6 header bytes
    Payload CRC:  CRC-32 via hardware
    
  STRINGS (not directly referenced by code - logging section):
    0x0B4842  "Firmware Load (0x8E)"
    0x0B4AE5  "CRC of update image passed"
    0x0B4B0C  "CRC of update image failed"
    0x0B3938  "VERIFY_FW_IMAGE unsupported revision: %u"
""")

print("=" * 78)
print("ANALYSIS COMPLETE")
print("=" * 78)

