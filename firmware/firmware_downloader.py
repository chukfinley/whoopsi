#!/usr/bin/env python3
"""
Whoop Firmware Downloader & Archiver
=====================================
Downloads firmware for all Whoop device types (GOOSE=5.0, HARVARD=Gen4, PUFFIN=Accessory)
from the Whoop API, parses .zbin headers, and maintains a version archive.

Usage:
  python3 firmware_downloader.py --email user@email.com --password pass
  python3 firmware_downloader.py --token eyJhbG...
  python3 firmware_downloader.py --check-only --token TOKEN  # just check versions
"""

import json
import os
import sys
import struct
import hashlib
import base64
import gzip
import zlib
import zipfile
import argparse
import getpass
import shutil
import time
import io
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# --- Constants ---
API_BASE = "https://api.prod.whoop.com"
COGNITO_URL = "https://api.prod.whoop.com/auth-service/v3/whoop/"
COGNITO_CLIENT_ID = os.environ.get("WHOOP_COGNITO_CLIENT_ID", "")
if not COGNITO_CLIENT_ID:
    print("Set WHOOP_COGNITO_CLIENT_ID env var (extract from Whoop APK)")
    # You can find the client ID by decompiling the official Whoop Android APK
    # and searching for "ClientId" in the Cognito auth flow.
FW_CHECK_URL = f"{API_BASE}/firmware-service/v4/firmware/check"
FW_VERSION_URL = f"{API_BASE}/firmware-service/v4/firmware/version"
APP_HEADERS = {
    "User-Agent": "Whoop-Android\\5.430.0",
    "x-whoop-app-version": "5.430.0",
    "x-whoop-app-version-code": "375528",
    "x-whoop-device-platform": "ANDROID",
    "x-whoop-package-name": "com.whoop.android",
}

DEVICE_TYPES = {
    "GOOSE": "Whoop 5.0",
    "HARVARD": "Whoop Gen4",
    "PUFFIN": "Accessory (Bicep/Boxer)",
}

CHIP_MAP = {
    "GOOSE": "AMBIQ",
    "HARVARD": "AMBIQ",
    "PUFFIN": "NORDIC",
}

SCRIPT_DIR = Path(__file__).parent.resolve()
ARCHIVE_DIR = SCRIPT_DIR / "archive"
VERSION_HISTORY = ARCHIVE_DIR / "version_history.json"


# --- Auth (from whoop_export.py) ---


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def login_cognito(email: str, password: str) -> str:
    print(f"  Logging in as {email}...")
    resp = requests.post(
        COGNITO_URL,
        json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": email, "PASSWORD": password},
        },
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": "Whoop-Android\\5.430.0",
        },
        timeout=30,
    )

    body = resp.json()
    if resp.status_code != 200:
        error_msg = body.get("message", "Unknown error")
        print(f"  Login failed: {error_msg}")
        sys.exit(1)

    challenge = body.get("ChallengeName")
    if challenge:
        print(f"  Auth challenge: {challenge}. Use --token instead.")
        sys.exit(1)

    token = body.get("AuthenticationResult", {}).get("AccessToken")
    if not token:
        print("  Login succeeded but no access token returned.")
        sys.exit(1)

    print("  Login successful.")
    return token


def get_auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **APP_HEADERS,
    }


# --- Firmware API ---


def check_firmware(token: str, device_name: str) -> dict | None:
    """Check available firmware for a device type."""
    chip = CHIP_MAP.get(device_name, "AMBIQ")
    payload = {
        "current_chip_firmwares": [{"chip_name": chip, "version": "0.0.0.0"}],
    }
    try:
        resp = requests.post(
            f"{FW_CHECK_URL}?deviceName={device_name}",
            json=payload,
            headers=get_auth_headers(token),
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"    Check failed: HTTP {resp.status_code} - {resp.text[:200]}")
    except requests.RequestException as e:
        print(f"    Check error: {e}")
    return None


def download_firmware(token: str, device_name: str, version: str) -> bytes | None:
    """Download firmware ZIP (Base64-encoded in JSON response)."""
    chip = CHIP_MAP.get(device_name, "AMBIQ")
    payload = {
        "current_chip_firmwares": [{"chip_name": chip, "version": "0.0.0.0"}],
        "chip_firmwares_of_upgrade": [{"chip_name": chip, "version": version}],
    }
    try:
        resp = requests.post(
            f"{FW_VERSION_URL}?deviceName={device_name}",
            json=payload,
            headers=get_auth_headers(token),
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            b64_zip = data.get("firmware_zip_file")
            if b64_zip:
                return base64.b64decode(b64_zip)
            print(f"    No firmware_zip_file in response")
        else:
            print(f"    Download failed: HTTP {resp.status_code} - {resp.text[:200]}")
    except requests.RequestException as e:
        print(f"    Download error: {e}")
    return None


# --- .zbin Parsing ---


def parse_zbin_header(data: bytes) -> dict:
    """Parse .zbin 512-byte header and extract metadata."""
    if len(data) < 512:
        return {"error": "File too small for zbin header"}

    header = data[:512]
    payload = data[512:]

    # Parse header fields
    payload_crc = struct.unpack_from("<I", header, 0x000)[0]
    compressed_size = struct.unpack_from("<I", header, 0x004)[0]
    compression_algo = struct.unpack_from("<I", header, 0x008)[0]
    encryption_algo = struct.unpack_from("<I", header, 0x00C)[0]
    image_type = struct.unpack_from("<I", header, 0x010)[0]

    # Build info
    build_info_raw = header[0x018:0x04C]
    build_info = build_info_raw.split(b"\x00")[0].decode("ascii", errors="replace")

    # Extract git hash and timestamp from build info
    git_hash = ""
    build_timestamp = ""
    if len(build_info_raw) > 4:
        # Format: #J\ni{git_hash}{timestamp}
        info_str = (
            build_info_raw[4:].split(b"\x00")[0].decode("ascii", errors="replace")
        )
        if len(info_str) >= 24:
            git_hash = info_str[:24]
            build_timestamp = info_str[24:]

    version_str = (
        header[0x04C:0x05C].split(b"\x00")[0].decode("ascii", errors="replace")
    )
    builder_machine = (
        header[0x064:0x07C].split(b"\x00")[0].decode("ascii", errors="replace")
    )
    ver_major = struct.unpack_from("<I", header, 0x07C)[0]
    ver_minor = struct.unpack_from("<I", header, 0x080)[0]
    ver_patch = struct.unpack_from("<I", header, 0x084)[0]

    total_size = struct.unpack_from("<I", header, 0x114)[0]
    header_size = struct.unpack_from("<I", header, 0x11C)[0]
    header_crc = struct.unpack_from("<I", header, 0x1F8)[0]
    payload_crc_copy = struct.unpack_from("<I", header, 0x1FC)[0]

    # Verify CRCs
    computed_payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    computed_header_crc = zlib.crc32(bytes(header[0x008:0x1F8])) & 0xFFFFFFFF

    crc_checks = {
        "payload_crc_match": computed_payload_crc == payload_crc,
        "header_crc_match": computed_header_crc == header_crc,
        "payload_crc_copy_match": payload_crc == payload_crc_copy,
        "size_match": compressed_size + header_size == total_size == len(data),
    }

    # Decompress to get raw binary size
    decompressed_size = 0
    try:
        decompressed = gzip.decompress(payload)
        decompressed_size = len(decompressed)
    except Exception:
        pass

    return {
        "version": f"{ver_major}.{ver_minor}.{ver_patch}.0",
        "version_string": version_str,
        "version_major": ver_major,
        "version_minor": ver_minor,
        "version_patch": ver_patch,
        "image_type": image_type,
        "compression_algo": compression_algo,
        "encryption_algo": encryption_algo,
        "build_info": build_info,
        "git_hash": git_hash,
        "build_timestamp": build_timestamp,
        "builder_machine": builder_machine,
        "compressed_size": compressed_size,
        "decompressed_size": decompressed_size,
        "total_size": total_size,
        "header_size": header_size,
        "payload_crc": f"0x{payload_crc:08X}",
        "header_crc": f"0x{header_crc:08X}",
        "crc_checks": crc_checks,
    }


def parse_bin_header(data: bytes) -> dict:
    """Parse Ambiq .bin header (decompressed firmware, same layout at offset 0)."""
    if len(data) < 0x200:
        return {"error": "File too small"}

    magic = struct.unpack_from("<I", data, 0x000)[0]
    payload_size = struct.unpack_from("<I", data, 0x004)[0]
    encryption_type = struct.unpack_from("<I", data, 0x008)[0]
    auth_algo = struct.unpack_from("<I", data, 0x00C)[0]
    auth_key_idx = struct.unpack_from("<I", data, 0x010)[0]

    build_info = data[0x018:0x04C].split(b"\x00")[0].decode("ascii", errors="replace")
    version_str = data[0x04C:0x05C].split(b"\x00")[0].decode("ascii", errors="replace")
    board = data[0x064:0x07C].split(b"\x00")[0].decode("ascii", errors="replace")

    ver_major = struct.unpack_from("<I", data, 0x07C)[0]
    ver_minor = struct.unpack_from("<I", data, 0x080)[0]
    ver_patch = struct.unpack_from("<I", data, 0x084)[0]

    # Vector table at 0x200
    sp_init = struct.unpack_from("<I", data, 0x200)[0] if len(data) > 0x204 else 0
    reset_handler = struct.unpack_from("<I", data, 0x204)[0] if len(data) > 0x208 else 0

    return {
        "magic": f"0x{magic:08X}",
        "payload_size": payload_size,
        "encryption_type": encryption_type,
        "auth_algo": auth_algo,
        "auth_key_idx": auth_key_idx,
        "build_info": build_info,
        "version": f"{ver_major}.{ver_minor}.{ver_patch}.0",
        "version_string": version_str,
        "board": board,
        "sp_init": f"0x{sp_init:08X}",
        "reset_handler": f"0x{reset_handler:08X}",
        "binary_size": len(data),
    }


# --- Archive Management ---


def load_version_history() -> dict:
    if VERSION_HISTORY.exists():
        with open(VERSION_HISTORY) as f:
            return json.load(f)
    return {"versions": {}, "last_check": None}


def save_version_history(history: dict):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(VERSION_HISTORY, "w") as f:
        json.dump(history, f, indent=2)


def archive_firmware(device_name: str, version: str, zip_data: bytes) -> dict:
    """Extract ZIP, parse .zbin, decompress to .bin, save to archive."""
    version_dir = ARCHIVE_DIR / device_name / version
    version_dir.mkdir(parents=True, exist_ok=True)

    # Save raw ZIP
    zip_path = version_dir / f"{device_name.lower()}_{version}.zip"
    with open(zip_path, "wb") as f:
        f.write(zip_data)

    result = {
        "device": device_name,
        "version": version,
        "zip_sha256": hashlib.sha256(zip_data).hexdigest(),
        "zip_size": len(zip_data),
        "files": [],
    }

    # Extract ZIP
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                extracted = zf.read(name)
                out_path = version_dir / name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(extracted)

                file_info = {
                    "name": name,
                    "size": len(extracted),
                    "sha256": hashlib.sha256(extracted).hexdigest(),
                }

                # Parse .zbin files
                if name.endswith(".zbin"):
                    zbin_meta = parse_zbin_header(extracted)
                    file_info["zbin_metadata"] = zbin_meta

                    # Decompress to .bin
                    if len(extracted) > 512:
                        try:
                            decompressed = gzip.decompress(extracted[512:])
                            bin_name = name.replace(".zbin", ".bin")
                            bin_path = version_dir / bin_name
                            with open(bin_path, "wb") as f:
                                f.write(decompressed)
                            bin_meta = parse_bin_header(decompressed)
                            file_info["bin_metadata"] = bin_meta
                            file_info["bin_name"] = bin_name
                            file_info["bin_size"] = len(decompressed)
                            file_info["bin_sha256"] = hashlib.sha256(
                                decompressed
                            ).hexdigest()
                            print(
                                f"    Decompressed: {bin_name} ({len(decompressed):,} bytes)"
                            )
                        except Exception as e:
                            print(f"    Decompress failed for {name}: {e}")

                # Parse standalone .bin files (e.g., Puffin Nordic DFU)
                elif name.endswith(".bin") and len(extracted) > 0x200:
                    file_info["bin_metadata"] = parse_bin_header(extracted)

                result["files"].append(file_info)
    except zipfile.BadZipFile:
        print(f"    Warning: Not a valid ZIP file, saving raw data")
        raw_path = version_dir / f"{device_name.lower()}_{version}.raw"
        with open(raw_path, "wb") as f:
            f.write(zip_data)

    # Save metadata
    meta_path = version_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def migrate_existing():
    """Migrate existing firmware files into the archive structure."""
    migrated = []

    # Migrate maverick v50.35.2.0
    maverick_dir = SCRIPT_DIR / "maverick_ambiq_50.35.2.0"
    if maverick_dir.exists():
        target = ARCHIVE_DIR / "GOOSE" / "50.35.2.0"
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            for f in maverick_dir.iterdir():
                shutil.copy2(f, target / f.name)

            # Also copy the ZIP if available
            zip_file = SCRIPT_DIR / "maverick_ambiq_50.35.2.0.zip"
            if zip_file.exists():
                shutil.copy2(zip_file, target / "maverick_ambiq_50.35.2.0.zip")

            # Generate metadata from existing files
            bin_path = target / "maverick-50.35.2.0.bin"
            zbin_path = target / "maverick-50.35.2.0.zbin"
            meta = {
                "device": "GOOSE",
                "version": "50.35.2.0",
                "migrated_from": str(maverick_dir),
                "migration_date": datetime.now(timezone.utc).isoformat(),
                "files": [],
            }
            if bin_path.exists():
                bin_data = bin_path.read_bytes()
                meta["files"].append(
                    {
                        "name": "maverick-50.35.2.0.bin",
                        "size": len(bin_data),
                        "sha256": hashlib.sha256(bin_data).hexdigest(),
                        "bin_metadata": parse_bin_header(bin_data),
                    }
                )
            if zbin_path.exists():
                zbin_data = zbin_path.read_bytes()
                meta["files"].append(
                    {
                        "name": "maverick-50.35.2.0.zbin",
                        "size": len(zbin_data),
                        "sha256": hashlib.sha256(zbin_data).hexdigest(),
                        "zbin_metadata": parse_zbin_header(zbin_data),
                    }
                )

            with open(target / "metadata.json", "w") as f:
                json.dump(meta, f, indent=2)

            migrated.append(("GOOSE", "50.35.2.0"))
            print(f"  Migrated: GOOSE/50.35.2.0 (Maverick)")

    # Migrate Puffin v3.30.5.0
    puffin_dir = SCRIPT_DIR / "puffin_3.30.5.0"
    if puffin_dir.exists():
        target = ARCHIVE_DIR / "PUFFIN" / "3.30.5.0"
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            for f in puffin_dir.iterdir():
                shutil.copy2(f, target / f.name)

            zip_file = SCRIPT_DIR / "puffin_3.30.5.0.zip"
            if zip_file.exists():
                shutil.copy2(zip_file, target / "puffin_3.30.5.0.zip")

            # Generate metadata
            meta = {
                "device": "PUFFIN",
                "version": "3.30.5.0",
                "migrated_from": str(puffin_dir),
                "migration_date": datetime.now(timezone.utc).isoformat(),
                "files": [],
            }
            for f in target.iterdir():
                if f.suffix == ".bin":
                    bin_data = f.read_bytes()
                    meta["files"].append(
                        {
                            "name": f.name,
                            "size": len(bin_data),
                            "sha256": hashlib.sha256(bin_data).hexdigest(),
                        }
                    )

            with open(target / "metadata.json", "w") as f:
                json.dump(meta, f, indent=2)

            migrated.append(("PUFFIN", "3.30.5.0"))
            print(f"  Migrated: PUFFIN/3.30.5.0")

    return migrated


# --- Main ---


def main():
    parser = argparse.ArgumentParser(
        description="Download and archive Whoop firmware versions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python3 firmware_downloader.py --email user@email.com
  python3 firmware_downloader.py --token eyJhbG...
  python3 firmware_downloader.py --check-only --token TOKEN
  python3 firmware_downloader.py --migrate-only
""",
    )
    parser.add_argument("--email", "-e", help="Whoop account email")
    parser.add_argument("--password", "-p", help="Password (prompted if omitted)")
    parser.add_argument("--token", "-t", help="Bearer token (skip login)")
    parser.add_argument(
        "--check-only", action="store_true", help="Only check versions, don't download"
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Only migrate existing files to archive",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        default=list(DEVICE_TYPES.keys()),
        choices=list(DEVICE_TYPES.keys()),
        help="Device types to check (default: all)",
    )
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  WHOOP FIRMWARE DOWNLOADER")
    print("=" * 60)
    print()

    # Migrate existing files first
    print("[1/4] Migrating existing firmware to archive...")
    migrated = migrate_existing()
    if not migrated:
        print("  No files to migrate (already done or not found)")
    print()

    if args.migrate_only:
        print("Migration complete.")
        return

    # Authenticate
    print("[2/4] Authenticating...")
    token = None
    if args.token:
        token = args.token.strip()
        print("  Using provided token.")
    elif args.email:
        password = args.password or getpass.getpass("  Password: ")
        token = login_cognito(args.email, password)
    else:
        print("  No credentials provided. Use --email or --token.")
        print("  Run with --migrate-only to only organize existing files.")
        sys.exit(1)
    print()

    # Check firmware versions
    print("[3/4] Checking firmware versions...")
    history = load_version_history()
    available = {}

    for device in args.devices:
        desc = DEVICE_TYPES[device]
        print(f"\n  {device} ({desc}):")
        result = check_firmware(token, device)
        if result:
            # Parse response for version info
            chips = result.get("chip_firmwares_of_upgrade", [])
            if not chips:
                chips = result.get("chip_firmwares", [])
            if chips:
                for chip in chips:
                    version = chip.get("version", "unknown")
                    chip_name = chip.get("chip_name", "?")
                    print(f"    Latest: {chip_name} v{version}")
                    available[device] = {
                        "version": version,
                        "chip_name": chip_name,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }

                    # Check if we already have this version
                    archive_path = ARCHIVE_DIR / device / version
                    if archive_path.exists():
                        print(f"    Status: Already archived")
                    else:
                        print(f"    Status: NEW - not yet downloaded")
            else:
                print(f"    No firmware info in response")
                print(f"    Response keys: {list(result.keys())}")
                # Store raw response for debugging
                available[device] = {
                    "version": "unknown",
                    "raw_response": result,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
        else:
            print(f"    Failed to check")
        time.sleep(0.5)

    # Update version history
    history["last_check"] = datetime.now(timezone.utc).isoformat()
    for device, info in available.items():
        key = f"{device}/{info['version']}"
        if key not in history["versions"]:
            history["versions"][key] = {
                "device": device,
                "version": info["version"],
                "first_seen": info["checked_at"],
                "last_seen": info["checked_at"],
            }
        else:
            history["versions"][key]["last_seen"] = info["checked_at"]
    save_version_history(history)
    print(f"\n  Version history saved to {VERSION_HISTORY}")

    if args.check_only:
        print("\n  Check complete (--check-only mode).")
        return

    # Download new firmware
    print("\n[4/4] Downloading new firmware...")
    downloaded = 0
    for device, info in available.items():
        version = info["version"]
        if version == "unknown":
            continue
        archive_path = ARCHIVE_DIR / device / version
        if archive_path.exists():
            print(f"\n  {device} v{version}: Already archived, skipping")
            continue

        print(f"\n  {device} v{version}: Downloading...")
        zip_data = download_firmware(token, device, version)
        if zip_data:
            print(f"    ZIP size: {len(zip_data):,} bytes")
            result = archive_firmware(device, version, zip_data)
            downloaded += 1

            print(f"    Archived to: {archive_path}")
            for f in result.get("files", []):
                name = f["name"]
                size = f["size"]
                print(f"      {name}: {size:,} bytes")
                if "zbin_metadata" in f:
                    meta = f["zbin_metadata"]
                    checks = meta.get("crc_checks", {})
                    all_ok = all(checks.values())
                    print(f"      CRC checks: {'ALL PASSED' if all_ok else 'FAILED!'}")
                    for check, ok in checks.items():
                        status = "OK" if ok else "FAIL"
                        print(f"        {check}: {status}")

            # Compare SHA256 with existing if same version
            existing_bin = (
                SCRIPT_DIR / f"maverick_ambiq_{version}" / f"maverick-{version}.bin"
            )
            if existing_bin.exists():
                existing_sha = hashlib.sha256(existing_bin.read_bytes()).hexdigest()
                for f in result.get("files", []):
                    if f.get("bin_sha256"):
                        if f["bin_sha256"] == existing_sha:
                            print(f"    SHA256 match with existing: IDENTICAL")
                        else:
                            print(f"    SHA256 MISMATCH with existing!")
                            print(f"      Existing: {existing_sha}")
                            print(f"      New:      {f['bin_sha256']}")
        else:
            print(f"    Download failed!")
        time.sleep(1)

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Devices checked: {len(available)}")
    print(f"  New downloads:   {downloaded}")

    # List archive
    print("\n  Archive contents:")
    if ARCHIVE_DIR.exists():
        for device_dir in sorted(ARCHIVE_DIR.iterdir()):
            if device_dir.is_dir() and device_dir.name in DEVICE_TYPES:
                for ver_dir in sorted(device_dir.iterdir()):
                    if ver_dir.is_dir():
                        meta_path = ver_dir / "metadata.json"
                        size = sum(
                            f.stat().st_size for f in ver_dir.rglob("*") if f.is_file()
                        )
                        print(f"    {device_dir.name}/{ver_dir.name}: {size:,} bytes")
    print()


if __name__ == "__main__":
    main()
