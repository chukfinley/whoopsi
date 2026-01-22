#!/usr/bin/env python3
"""
Whoop Firmware Patcher
======================
Apply targeted patches to firmware binaries: replace instructions,
change constants, NOP out code. Recalculates all CRCs and can repackage as .zbin.

Usage:
  python3 firmware_patcher.py firmware.bin --patch 0x1234:NOP --patch 0x5678:DEADBEEF
  python3 firmware_patcher.py firmware.bin --patch-file patches.json -o patched.bin
  python3 firmware_patcher.py firmware.bin --patch 0x1234:NOP --zbin -o patched.zbin

WARNING: Patched firmware may brick your device. Use at your own risk.
         The SBL (Secure Boot Loader) may reject modified images.
"""
import struct
import gzip
import zlib
import hashlib
import json
import argparse
import sys
from pathlib import Path


# ARM Thumb-2 NOP = 0xBF00 (2 bytes), NOP.W = 0xF3AF8000 (4 bytes)
THUMB_NOP = bytes([0x00, 0xBF])
THUMB_NOP_W = bytes([0xAF, 0xF3, 0x00, 0x80])


def apply_patch(data: bytearray, offset: int, patch_bytes: bytes, description: str = "") -> dict:
    """Apply a single patch at the given offset."""
    if offset + len(patch_bytes) > len(data):
        return {"success": False, "error": f"Patch at 0x{offset:06X} exceeds binary size"}

    old_bytes = bytes(data[offset:offset + len(patch_bytes)])
    data[offset:offset + len(patch_bytes)] = patch_bytes

    return {
        "success": True,
        "offset": offset,
        "length": len(patch_bytes),
        "old_hex": old_bytes.hex(),
        "new_hex": patch_bytes.hex(),
        "description": description,
    }


def parse_patch_spec(spec: str) -> tuple:
    """Parse a patch specification string.

    Formats:
      0x1234:NOP          - 2-byte NOP at offset
      0x1234:NOP4          - 4-byte NOP.W at offset
      0x1234:DEADBEEF     - Raw hex bytes at offset
      0x1234:u32:12345    - 32-bit LE unsigned int
      0x1234:u16:255      - 16-bit LE unsigned int
      0x1234:str:hello    - ASCII string
    """
    parts = spec.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"Invalid patch format: {spec}")

    offset = int(parts[0], 0)

    if parts[1].upper() == "NOP":
        return offset, THUMB_NOP, "NOP (2 bytes)"
    elif parts[1].upper() == "NOP4":
        return offset, THUMB_NOP_W, "NOP.W (4 bytes)"
    elif parts[1].startswith("u32:"):
        val = int(parts[1][4:], 0)
        return offset, struct.pack("<I", val), f"u32={val}"
    elif parts[1].startswith("u16:"):
        val = int(parts[1][4:], 0)
        return offset, struct.pack("<H", val), f"u16={val}"
    elif parts[1].startswith("str:"):
        text = parts[1][4:]
        return offset, text.encode("ascii") + b'\x00', f'str="{text}"'
    else:
        # Raw hex bytes
        hex_str = parts[1] if len(parts) == 2 else parts[1]
        patch_bytes = bytes.fromhex(hex_str)
        desc = parts[2] if len(parts) > 2 else f"raw hex ({len(patch_bytes)} bytes)"
        return offset, patch_bytes, desc


def recalculate_bin_header_crc(data: bytearray):
    """Recalculate CRC in the .bin header at offset 0x1F8."""
    if len(data) >= 0x200:
        # Header CRC covers bytes 0x008:0x1F8
        crc = zlib.crc32(bytes(data[0x008:0x1F8])) & 0xFFFFFFFF
        struct.pack_into('<I', data, 0x1F8, crc)
        return crc
    return None


def build_patched_zbin(arm_binary: bytes) -> bytes:
    """Build a .zbin from a patched ARM binary, preserving original header fields."""
    compressed = gzip.compress(arm_binary, compresslevel=9)
    header = bytearray(512)

    payload_crc = zlib.crc32(compressed) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x000, payload_crc)
    struct.pack_into('<I', header, 0x004, len(compressed))

    # Copy metadata from the ARM binary's header (first 512 bytes)
    if len(arm_binary) >= 0x200:
        # Copy fields 0x008-0x110 from the binary header
        header[0x008:0x110] = arm_binary[0x008:0x110]

    struct.pack_into('<I', header, 0x110, 5)  # algo flags
    total_size = 512 + len(compressed)
    struct.pack_into('<I', header, 0x114, total_size)
    struct.pack_into('<I', header, 0x118, total_size - 2)
    struct.pack_into('<I', header, 0x11C, 512)
    header[0x160:0x1F8] = b'\xFF' * (0x1F8 - 0x160)

    hdr_crc = zlib.crc32(bytes(header[0x008:0x1F8])) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x1F8, hdr_crc)
    struct.pack_into('<I', header, 0x1FC, payload_crc)

    return bytes(header) + compressed


def main():
    parser = argparse.ArgumentParser(
        description="Apply patches to Whoop firmware binaries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
patch formats:
  OFFSET:NOP           2-byte Thumb NOP
  OFFSET:NOP4          4-byte Thumb NOP.W
  OFFSET:HEXBYTES      Raw hex bytes
  OFFSET:u32:VALUE     32-bit LE integer
  OFFSET:u16:VALUE     16-bit LE integer
  OFFSET:str:TEXT      ASCII string (null-terminated)

examples:
  python3 firmware_patcher.py fw.bin --patch 0x1234:NOP
  python3 firmware_patcher.py fw.bin --patch 0x1234:DEADBEEF --patch 0x5678:u32:42
  python3 firmware_patcher.py fw.bin --patch-file patches.json --zbin -o patched.zbin
""")
    parser.add_argument("input", help="Input firmware .bin file")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--patch", "-p", action="append", default=[],
                        help="Patch spec (OFFSET:DATA), can be repeated")
    parser.add_argument("--patch-file", help="JSON file with patch list")
    parser.add_argument("--zbin", action="store_true",
                        help="Output as .zbin (compress + header)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be changed without writing")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    data = bytearray(input_path.read_bytes())
    original_sha = hashlib.sha256(bytes(data)).hexdigest()
    print(f"Input:  {input_path} ({len(data):,} bytes)")
    print(f"SHA256: {original_sha}")

    # Collect patches
    patches = []
    for spec in args.patch:
        try:
            offset, patch_bytes, desc = parse_patch_spec(spec)
            patches.append({"offset": offset, "bytes": patch_bytes.hex(), "description": desc})
        except (ValueError, IndexError) as e:
            print(f"Error parsing patch '{spec}': {e}")
            sys.exit(1)

    if args.patch_file:
        pf = Path(args.patch_file)
        if not pf.exists():
            print(f"Error: patch file {pf} not found")
            sys.exit(1)
        with open(pf) as f:
            file_patches = json.load(f)
        if isinstance(file_patches, list):
            patches.extend(file_patches)
        elif isinstance(file_patches, dict) and "patches" in file_patches:
            patches.extend(file_patches["patches"])

    if not patches:
        print("No patches specified. Use --patch or --patch-file.")
        sys.exit(1)

    print(f"\nApplying {len(patches)} patches:")
    results = []
    for p in patches:
        offset = p["offset"] if isinstance(p["offset"], int) else int(p["offset"], 0)
        patch_bytes = bytes.fromhex(p["bytes"])
        desc = p.get("description", "")

        if args.dry_run:
            old_hex = bytes(data[offset:offset + len(patch_bytes)]).hex()
            print(f"  [DRY] 0x{offset:06X}: {old_hex} -> {patch_bytes.hex()} ({desc})")
            results.append({"success": True, "dry_run": True, "offset": offset})
        else:
            result = apply_patch(data, offset, patch_bytes, desc)
            results.append(result)
            if result["success"]:
                print(f"  [OK]  0x{offset:06X}: {result['old_hex']} -> {result['new_hex']} ({desc})")
            else:
                print(f"  [ERR] 0x{offset:06X}: {result['error']}")

    if args.dry_run:
        print("\nDry run complete. No files modified.")
        return

    # Recalculate header CRC
    print("\nRecalculating header CRC32...")
    new_crc = recalculate_bin_header_crc(data)
    if new_crc is not None:
        print(f"  New header CRC: 0x{new_crc:08X}")

    patched_sha = hashlib.sha256(bytes(data)).hexdigest()
    print(f"\nPatched SHA256: {patched_sha}")

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    elif args.zbin:
        output_path = input_path.with_suffix(".patched.zbin")
    else:
        output_path = input_path.with_suffix(".patched.bin")

    # Write output
    if args.zbin:
        print(f"\nBuilding .zbin...")
        zbin_data = build_patched_zbin(bytes(data))
        output_path.write_bytes(zbin_data)
        print(f"Output: {output_path} ({len(zbin_data):,} bytes)")
    else:
        output_path.write_bytes(bytes(data))
        print(f"Output: {output_path} ({len(data):,} bytes)")

    # Save patch log
    log_path = output_path.with_suffix(".patches.json")
    log = {
        "input": str(input_path),
        "input_sha256": original_sha,
        "output": str(output_path),
        "output_sha256": patched_sha,
        "patches_applied": results,
        "header_crc": f"0x{new_crc:08X}" if new_crc else None,
    }
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"Patch log: {log_path}")


if __name__ == "__main__":
    main()
