#!/usr/bin/env python3
"""
Whoop .zbin Builder / Verifier / Extractor
==========================================
Build .zbin firmware images from ARM binaries, verify existing .zbin files,
or extract the ARM binary from a .zbin.

Based on the documented Ambiq OTA container format (512-byte header + gzip payload).

Usage:
  python3 zbin_builder.py --verify path/to/firmware.zbin
  python3 zbin_builder.py --extract path/to/firmware.zbin -o output.bin
  python3 zbin_builder.py --build path/to/firmware.bin -o output.zbin --version 50.35.3
"""
import struct
import gzip
import zlib
import hashlib
import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone


def build_zbin(arm_binary: bytes, ver_major: int = 50, ver_minor: int = 35,
               ver_patch: int = 2, image_type: int = 0x0D,
               git_hash: str = "", builder: str = "custom") -> bytes:
    """Build a .zbin file from an ARM binary.

    Args:
        arm_binary: Raw ARM Cortex-M4F binary
        ver_major/minor/patch: Version numbers
        image_type: 0x0D = main application
        git_hash: 24-char git commit hash (or empty)
        builder: Builder machine name
    Returns:
        Complete .zbin file bytes
    """
    # Compress the ARM binary
    compressed = gzip.compress(arm_binary, compresslevel=9)

    # Build 512-byte header
    header = bytearray(512)

    # Payload CRC32 (offset 0x000)
    payload_crc = zlib.crc32(compressed) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x000, payload_crc)

    # Compressed payload size (offset 0x004)
    struct.pack_into('<I', header, 0x004, len(compressed))

    # Compression algorithm (offset 0x008) — 5 = gzip
    struct.pack_into('<I', header, 0x008, 5)

    # Encryption algorithm (offset 0x00C) — 5 = none
    struct.pack_into('<I', header, 0x00C, 5)

    # Image type (offset 0x010)
    struct.pack_into('<I', header, 0x010, image_type)

    # Reserved (offset 0x014)
    struct.pack_into('<I', header, 0x014, 0)

    # Build info (offset 0x018, 52 bytes)
    build_marker = b'#J\ni'
    if not git_hash:
        git_hash = "0" * 24
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    build_info = build_marker + git_hash.encode()[:24] + timestamp.encode()[:19]
    header[0x018:0x018 + len(build_info)] = build_info[:52]

    # Version string (offset 0x04C, 16 bytes)
    ver_str = f"{ver_major}.{ver_minor}.x.x".encode()[:15]
    header[0x04C:0x04C + len(ver_str)] = ver_str

    # Builder machine name (offset 0x064, 24 bytes)
    builder_bytes = builder.encode()[:23]
    header[0x064:0x064 + len(builder_bytes)] = builder_bytes

    # Version numbers (offsets 0x07C, 0x080, 0x084)
    struct.pack_into('<I', header, 0x07C, ver_major)
    struct.pack_into('<I', header, 0x080, ver_minor)
    struct.pack_into('<I', header, 0x084, ver_patch)

    # Zero padding 0x088-0x10F (already zero)

    # Algorithm flags repeat (offset 0x110)
    struct.pack_into('<I', header, 0x110, 5)

    # Total image size (offset 0x114) = header + compressed payload
    total_size = 512 + len(compressed)
    struct.pack_into('<I', header, 0x114, total_size)

    # Total size - 2 (offset 0x118)
    struct.pack_into('<I', header, 0x118, total_size - 2)

    # Header size (offset 0x11C) = 512
    struct.pack_into('<I', header, 0x11C, 512)

    # Zero padding 0x120-0x15F (already zero)

    # Erased flash fill 0x160-0x1F7
    header[0x160:0x1F8] = b'\xFF' * (0x1F8 - 0x160)

    # Header CRC32 (offset 0x1F8) = CRC32(bytes 0x008:0x1F8)
    hdr_crc = zlib.crc32(bytes(header[0x008:0x1F8])) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x1F8, hdr_crc)

    # Payload CRC32 copy (offset 0x1FC)
    struct.pack_into('<I', header, 0x1FC, payload_crc)

    return bytes(header) + compressed


def verify_zbin(data: bytes) -> dict:
    """Verify all three .zbin integrity checks.

    Returns dict with check results and parsed metadata.
    """
    if len(data) < 512:
        return {"valid": False, "error": "File too small (< 512 bytes)"}

    header = data[:512]
    payload = data[512:]

    # Parse header fields
    payload_crc_stored = struct.unpack_from('<I', header, 0x000)[0]
    compressed_size = struct.unpack_from('<I', header, 0x004)[0]
    compression_algo = struct.unpack_from('<I', header, 0x008)[0]
    encryption_algo = struct.unpack_from('<I', header, 0x00C)[0]
    image_type = struct.unpack_from('<I', header, 0x010)[0]
    total_size = struct.unpack_from('<I', header, 0x114)[0]
    header_size = struct.unpack_from('<I', header, 0x11C)[0]
    header_crc_stored = struct.unpack_from('<I', header, 0x1F8)[0]
    payload_crc_copy = struct.unpack_from('<I', header, 0x1FC)[0]

    ver_major = struct.unpack_from('<I', header, 0x07C)[0]
    ver_minor = struct.unpack_from('<I', header, 0x080)[0]
    ver_patch = struct.unpack_from('<I', header, 0x084)[0]
    version_str = header[0x04C:0x05C].split(b'\x00')[0].decode('ascii', errors='replace')
    builder = header[0x064:0x07C].split(b'\x00')[0].decode('ascii', errors='replace')

    build_info_raw = header[0x018:0x04C]
    git_hash = ""
    build_timestamp = ""
    if len(build_info_raw) > 4:
        info = build_info_raw[4:].split(b'\x00')[0].decode('ascii', errors='replace')
        if len(info) >= 24:
            git_hash = info[:24]
            build_timestamp = info[24:]

    # Check 1: Payload CRC32
    computed_payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    payload_crc_ok = computed_payload_crc == payload_crc_stored

    # Check 2: Header CRC32
    computed_header_crc = zlib.crc32(bytes(header[0x008:0x1F8])) & 0xFFFFFFFF
    header_crc_ok = computed_header_crc == header_crc_stored

    # Check 3: Size consistency
    size_ok = (compressed_size + header_size == total_size == len(data))

    # Bonus: payload CRC copy match
    copy_ok = payload_crc_stored == payload_crc_copy

    # Decompress test
    decompressed_size = 0
    decompress_ok = False
    try:
        decompressed = gzip.decompress(payload)
        decompressed_size = len(decompressed)
        decompress_ok = True
    except Exception as e:
        decompress_ok = False

    all_ok = payload_crc_ok and header_crc_ok and size_ok and copy_ok

    return {
        "valid": all_ok,
        "file_size": len(data),
        "version": f"{ver_major}.{ver_minor}.{ver_patch}.0",
        "version_string": version_str,
        "builder": builder,
        "git_hash": git_hash,
        "build_timestamp": build_timestamp,
        "image_type": image_type,
        "compression_algo": compression_algo,
        "encryption_algo": encryption_algo,
        "compressed_size": compressed_size,
        "decompressed_size": decompressed_size,
        "checks": {
            "payload_crc": {
                "passed": payload_crc_ok,
                "stored": f"0x{payload_crc_stored:08X}",
                "computed": f"0x{computed_payload_crc:08X}",
            },
            "header_crc": {
                "passed": header_crc_ok,
                "stored": f"0x{header_crc_stored:08X}",
                "computed": f"0x{computed_header_crc:08X}",
            },
            "size_consistency": {
                "passed": size_ok,
                "compressed_size": compressed_size,
                "header_size": header_size,
                "total_size": total_size,
                "actual_file_size": len(data),
            },
            "payload_crc_copy": {
                "passed": copy_ok,
                "offset_0x000": f"0x{payload_crc_stored:08X}",
                "offset_0x1FC": f"0x{payload_crc_copy:08X}",
            },
            "decompression": {
                "passed": decompress_ok,
                "decompressed_size": decompressed_size,
            },
        },
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def extract_zbin(data: bytes) -> bytes:
    """Extract the decompressed ARM binary from a .zbin file."""
    if len(data) < 512:
        raise ValueError("File too small for .zbin header")
    payload = data[512:]
    return gzip.decompress(payload)


def main():
    parser = argparse.ArgumentParser(
        description="Whoop .zbin firmware tool: build, verify, or extract.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python3 zbin_builder.py --verify firmware.zbin
  python3 zbin_builder.py --extract firmware.zbin -o firmware.bin
  python3 zbin_builder.py --build firmware.bin -o firmware.zbin --version 50.35.3
""")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", metavar="BIN", help="Build .zbin from ARM binary")
    group.add_argument("--verify", metavar="ZBIN", help="Verify .zbin integrity")
    group.add_argument("--extract", metavar="ZBIN", help="Extract ARM binary from .zbin")

    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--version", default="50.35.2",
                        help="Version string (major.minor.patch, default: 50.35.2)")
    parser.add_argument("--image-type", type=lambda x: int(x, 0), default=0x0D,
                        help="Image type (default: 0x0D = main app)")
    parser.add_argument("--git-hash", default="", help="Git commit hash (24 hex chars)")
    parser.add_argument("--builder", default="custom", help="Builder machine name")
    args = parser.parse_args()

    if args.verify:
        print(f"Verifying: {args.verify}")
        data = Path(args.verify).read_bytes()
        result = verify_zbin(data)

        print(f"\n  File:    {args.verify}")
        print(f"  Size:    {result['file_size']:,} bytes")
        print(f"  Version: {result['version']}")
        print(f"  Builder: {result['builder']}")
        print(f"  Git:     {result['git_hash']}")
        print(f"  Built:   {result['build_timestamp']}")
        print(f"  SHA256:  {result['sha256']}")
        print(f"  Decompressed: {result['decompressed_size']:,} bytes")
        print()

        all_ok = True
        for check_name, check in result["checks"].items():
            status = "PASS" if check["passed"] else "FAIL"
            symbol = "+" if check["passed"] else "X"
            print(f"  [{symbol}] {check_name}: {status}")
            if not check["passed"]:
                all_ok = False
                for k, v in check.items():
                    if k != "passed":
                        print(f"      {k}: {v}")

        print()
        if all_ok:
            print("  RESULT: ALL CHECKS PASSED")
        else:
            print("  RESULT: VERIFICATION FAILED")
            sys.exit(1)

    elif args.extract:
        print(f"Extracting: {args.extract}")
        data = Path(args.extract).read_bytes()

        # Verify first
        result = verify_zbin(data)
        if not result["valid"]:
            print("  WARNING: .zbin verification failed! Extracting anyway.")

        arm_binary = extract_zbin(data)
        output = args.output or args.extract.replace(".zbin", ".bin")
        Path(output).write_bytes(arm_binary)
        print(f"  Extracted: {output} ({len(arm_binary):,} bytes)")
        print(f"  SHA256:    {hashlib.sha256(arm_binary).hexdigest()}")

    elif args.build:
        print(f"Building .zbin from: {args.build}")
        arm_binary = Path(args.build).read_bytes()
        print(f"  Input size: {len(arm_binary):,} bytes")

        # Parse version
        parts = args.version.split(".")
        ver_major = int(parts[0]) if len(parts) > 0 else 50
        ver_minor = int(parts[1]) if len(parts) > 1 else 35
        ver_patch = int(parts[2]) if len(parts) > 2 else 2

        zbin = build_zbin(
            arm_binary,
            ver_major=ver_major,
            ver_minor=ver_minor,
            ver_patch=ver_patch,
            image_type=args.image_type,
            git_hash=args.git_hash,
            builder=args.builder,
        )

        output = args.output or args.build.replace(".bin", ".zbin")
        Path(output).write_bytes(zbin)
        print(f"  Output:    {output} ({len(zbin):,} bytes)")
        print(f"  Compressed: {len(zbin) - 512:,} bytes ({100 * (len(zbin) - 512) / len(arm_binary):.1f}%)")

        # Verify the built file
        print("\n  Verifying built .zbin...")
        result = verify_zbin(zbin)
        for check_name, check in result["checks"].items():
            status = "PASS" if check["passed"] else "FAIL"
            print(f"    [{'+' if check['passed'] else 'X'}] {check_name}: {status}")

        # Verify round-trip
        extracted = extract_zbin(zbin)
        if extracted == arm_binary:
            print("    [+] Round-trip: PASS (extract matches input)")
        else:
            print("    [X] Round-trip: FAIL (extract differs from input!)")
            sys.exit(1)


if __name__ == "__main__":
    main()
