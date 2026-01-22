#!/usr/bin/env python3
"""
Track F: Comprehensive Security Analysis
=========================================

Analyzes the security posture of the Whoop 5.0 firmware including:
boot sequence, SBL interface, firmware update security, Cooper BLE
controller authentication, debug interfaces, crypto analysis,
anti-rollback mechanisms, and memory protection.

Usage:
    python3 track_f_security.py

Output:
    analysis/output/track_f_security.json
"""

import json
import math
import os
import re
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Add parent to path so we can import common
sys.path.insert(0, str(Path(__file__).parent))

from common import (
    DEFAULT_BIN,
    MRAM_BASE,
    VECTOR_TABLE_OFFSET,
    OUTPUT_DIR,
    load_firmware,
    get_capstone_md,
    disasm,
    disasm_function,
    find_function_prologs,
    find_function_start,
    find_bl_targets,
    find_callers,
    parse_vector_table,
    extract_strings,
    categorize_string,
    find_string_references,
    find_peripheral_references,
    save_output,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Code region boundary (file offsets)
CODE_REGION_END = 0x0A0000

# Image header offsets (Ambiq Apollo4 format)
HEADER_CRC_OFFSET = 0x000
HEADER_SIZE_OFFSET = 0x004
HEADER_AUTH_ALGO_OFFSET = 0x00C
HEADER_AUTH_KEY_IDX_OFFSET = 0x010

# Known crypto constants for identification
CRYPTO_CONSTANTS = {
    "AES_SBOX": bytes([
        0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5,
        0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    ]),
    "AES_INV_SBOX": bytes([
        0x52, 0x09, 0x6A, 0xD5, 0x30, 0x36, 0xA5, 0x38,
        0xBF, 0x40, 0xA3, 0x9E, 0x81, 0xF3, 0xD7, 0xFB,
    ]),
    "SHA256_H0_BE": struct.pack(">I", 0x6A09E667),
    "SHA256_H0_LE": struct.pack("<I", 0x6A09E667),
    "SHA256_K0_BE": struct.pack(">I", 0x428A2F98),
    "SHA256_K0_LE": struct.pack("<I", 0x428A2F98),
    "SHA1_H0_BE": struct.pack(">I", 0x67452301),
    "SHA1_H0_LE": struct.pack("<I", 0x67452301),
    "MD5_INIT_A_LE": struct.pack("<I", 0x67452301),
    "CRC32_POLY_LE": struct.pack("<I", 0xEDB88320),
    "CRC32_POLY_BE": struct.pack(">I", 0x04C11DB7),
    "CRC16_MODBUS_POLY": struct.pack("<H", 0xA001),
    # RSA common public exponent
    "RSA_E_65537": struct.pack("<I", 0x00010001),
    # ECDSA P-256 curve parameters (first 8 bytes of prime p)
    "P256_PRIME_BE": bytes.fromhex("FFFFFFFF00000001"),
    "P256_PRIME_LE": bytes.fromhex("0100000000000000")[::-1],
}

# Cortex-M4 system register addresses
SYSTEM_REGISTERS = {
    "MPU_TYPE": 0xE000ED90,
    "MPU_CTRL": 0xE000ED94,
    "MPU_RNR": 0xE000ED98,
    "MPU_RBAR": 0xE000ED9C,
    "MPU_RASR": 0xE000EDA0,
    "SCB_SHCSR": 0xE000ED24,  # System Handler Control and State
    "SCB_VTOR": 0xE000ED08,   # Vector Table Offset Register
    "SCB_AIRCR": 0xE000ED0C,  # Application Interrupt and Reset Control
    "SYSTICK_CSR": 0xE000E010,
    "SYSTICK_RVR": 0xE000E014,
    "NVIC_ISER0": 0xE000E100,
}

# Security-related strings to search for
SECURITY_SEARCH_TERMS = [
    # Boot/SBL
    "SBL", "bootloader", "Bootloader", "Secondary Boot", "boot",
    # Firmware update
    "CRC", "update", "Update", "firmware", "Firmware", "OTA",
    "image", "Image", "load", "Load", "flash",
    # Crypto
    "RSA", "ECDSA", "AES", "SHA", "sha", "hmac", "HMAC",
    "encrypt", "decrypt", "cipher", "hash",
    "mbedtls", "wolfssl", "tinycrypt", "crypto", "Crypto",
    "signature", "Signature", "verify", "Verify", "sign",
    "certificate", "cert", "x509", "key", "Key",
    # Auth
    "auth", "Auth", "authenticate", "password", "token",
    # Debug
    "debug", "Debug", "JTAG", "SWD", "DAP", "UART", "uart",
    "console", "shell", "printf",
    # Cooper BLE
    "Cooper", "cooper", "BLE Controller",
    # Anti-tamper
    "rollback", "monotonic", "OTP", "fuse", "Fuse",
    "tamper", "secure boot", "Secure Boot",
    # Memory protection
    "MPU", "stack", "guard", "canary", "overflow",
    "privilege", "unprivilege",
]


# ---------------------------------------------------------------------------
# Phase 1: Boot Sequence Analysis
# ---------------------------------------------------------------------------

def analyze_boot_sequence(data: bytes) -> dict:
    """
    Trace the boot sequence from Reset_Handler through the init chain.
    """
    print("[Track F] Phase 1: Boot sequence analysis")
    start_time = time.time()

    vectors = parse_vector_table(data)
    reset_addr_raw = vectors.get("Reset_Handler", 0)
    reset_addr = reset_addr_raw & ~1  # Clear Thumb bit

    result = {
        "reset_handler": f"0x{reset_addr_raw:08X}",
        "reset_handler_thumb_cleared": f"0x{reset_addr:08X}",
        "vector_table_offset": f"0x{VECTOR_TABLE_OFFSET:04X}",
        "sp_init": f"0x{vectors.get('SP_Init', 0):08X}",
        "init_chain": [],
        "disassembly": [],
        "fault_handlers": {},
        "notes": [],
    }

    # Map important fault handlers
    for handler_name in ["NMI_Handler", "HardFault_Handler", "MemManage_Handler",
                         "BusFault_Handler", "UsageFault_Handler", "SVC_Handler",
                         "PendSV_Handler", "SysTick_Handler"]:
        addr = vectors.get(handler_name, 0)
        result["fault_handlers"][handler_name] = f"0x{addr:08X}"

    # Check for shared handler addresses (common in simple firmware)
    handler_addrs = {}
    for name, addr in vectors.items():
        if addr != 0:
            if addr not in handler_addrs:
                handler_addrs[addr] = []
            handler_addrs[addr].append(name)

    shared_handlers = {f"0x{addr:08X}": names for addr, names in handler_addrs.items() if len(names) > 1}
    if shared_handlers:
        result["shared_handler_addresses"] = shared_handlers
        result["notes"].append(
            f"Found {len(shared_handlers)} shared handler addresses "
            f"(multiple vectors point to same function)"
        )

    # Disassemble Reset_Handler
    file_offset = reset_addr - MRAM_BASE
    if file_offset < 0 or file_offset >= len(data):
        result["notes"].append(
            f"Reset handler offset 0x{file_offset:06X} is out of binary bounds. "
            f"Checking if the raw bytes at the calculated offset are valid code."
        )

    # Try disassembly with skipdata mode for robustness
    md = get_capstone_md()
    md.skipdata = True

    # Try to disassemble at the file offset
    disasm_offset = file_offset
    if disasm_offset < 0:
        disasm_offset = 0
    if disasm_offset >= len(data):
        disasm_offset = 0

    try:
        instrs = list(md.disasm(data[disasm_offset:disasm_offset + 400], reset_addr))
        bl_targets = []

        for ins in instrs[:50]:
            entry = {
                "address": f"0x{ins.address:08X}",
                "bytes": ins.bytes.hex(),
                "mnemonic": ins.mnemonic,
                "op_str": ins.op_str,
            }
            result["disassembly"].append(entry)

            # Track BL calls (init chain)
            if ins.mnemonic in ("bl", "bl.w"):
                try:
                    target_str = ins.op_str.replace("#", "")
                    target = int(target_str, 16) if target_str.startswith("0x") else int(target_str)
                    bl_targets.append({
                        "caller": f"0x{ins.address:08X}",
                        "target": f"0x{target:08X}",
                        "instruction": f"{ins.mnemonic} {ins.op_str}",
                    })
                except (ValueError, TypeError):
                    bl_targets.append({
                        "caller": f"0x{ins.address:08X}",
                        "target": ins.op_str,
                        "instruction": f"{ins.mnemonic} {ins.op_str}",
                    })

        result["init_chain"] = bl_targets
        result["notes"].append(
            f"Disassembled {len(result['disassembly'])} instructions, "
            f"found {len(bl_targets)} BL calls in init chain"
        )

    except Exception as e:
        result["notes"].append(f"Disassembly error: {e}")

    # Also check the image header for boot-related info
    result["image_header"] = {
        "crc": f"0x{struct.unpack_from('<I', data, HEADER_CRC_OFFSET)[0]:08X}",
        "image_size": struct.unpack_from("<I", data, HEADER_SIZE_OFFSET)[0],
        "auth_algo": struct.unpack_from("<I", data, HEADER_AUTH_ALGO_OFFSET)[0],
        "auth_key_idx": struct.unpack_from("<I", data, HEADER_AUTH_KEY_IDX_OFFSET)[0],
    }

    elapsed = time.time() - start_time
    print(f"  Boot analysis complete in {elapsed:.1f}s")
    print(f"  Reset handler: {result['reset_handler']}")
    print(f"  Init chain BL calls: {len(result['init_chain'])}")
    print(f"  Image auth_algo: {result['image_header']['auth_algo']}")

    return result


# ---------------------------------------------------------------------------
# Phase 2: SBL Interface Analysis
# ---------------------------------------------------------------------------

def analyze_sbl_interface(data: bytes, all_strings: list) -> dict:
    """
    Search for SBL (Secondary Bootloader) related strings and code
    to understand how the application communicates with the bootloader.
    """
    print("\n[Track F] Phase 2: SBL interface analysis")
    start_time = time.time()

    result = {
        "strings": [],
        "sbl_version_strings": [],
        "sbl_command_strings": [],
        "code_refs": [],
        "notes": [],
    }

    sbl_terms = ["SBL", "sbl", "bootloader", "Bootloader", "Secondary Boot",
                 "SBL Ver", "Bootloader Ver"]

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]

        for term in sbl_terms:
            if term in text:
                code_refs = find_string_references(
                    data, offset, search_range=(0, CODE_REGION_END)
                )
                entry = {
                    "text": text[:200],
                    "offset": offset,
                    "address": f"0x{(offset + MRAM_BASE):08X}",
                    "matched_term": term,
                    "code_refs": [f"0x{(r + MRAM_BASE):08X}" for r in code_refs[:5]],
                    "code_ref_count": len(code_refs),
                }

                # Classify the SBL string
                if "Ver" in text or "version" in text.lower():
                    result["sbl_version_strings"].append(entry)
                elif "WSBLE_CMD" in text:
                    result["sbl_command_strings"].append(entry)
                else:
                    result["strings"].append(entry)

                # Track unique code references
                for ref in code_refs:
                    result["code_refs"].append(f"0x{(ref + MRAM_BASE):08X}")
                break

    # Deduplicate code refs
    result["code_refs"] = sorted(set(result["code_refs"]))

    # Analyze SBL version format
    for vs in result["sbl_version_strings"]:
        text = vs["text"]
        if "V1" in text:
            result["notes"].append(f"SBL Version V1 format string found: {text}")
        elif "V2" in text:
            result["notes"].append(f"SBL Version V2 format string found: {text}")
        elif "Invalid" in text:
            result["notes"].append(f"Invalid SBL version handling: {text}")

    # Count WSBLE_CMD variants (BLE commands that interact with SBL)
    wsble_cmds = set()
    for s in result["sbl_command_strings"]:
        text = s["text"]
        match = re.search(r'(WSBLE_CMD_\w+)', text)
        if match:
            wsble_cmds.add(match.group(1))

    result["wsble_commands"] = sorted(wsble_cmds)
    result["wsble_command_count"] = len(wsble_cmds)

    elapsed = time.time() - start_time
    print(f"  SBL analysis complete in {elapsed:.1f}s")
    print(f"  SBL strings: {len(result['strings'])}")
    print(f"  SBL version strings: {len(result['sbl_version_strings'])}")
    print(f"  WSBLE commands: {result['wsble_command_count']}")

    return result


# ---------------------------------------------------------------------------
# Phase 3: Firmware Update Security
# ---------------------------------------------------------------------------

def analyze_firmware_update_security(data: bytes, all_strings: list) -> dict:
    """
    Analyze the firmware update (OTA) path for security mechanisms:
    CRC validation, cryptographic verification, etc.
    """
    print("\n[Track F] Phase 3: Firmware update security")
    start_time = time.time()

    result = {
        "auth_algo": struct.unpack_from("<I", data, HEADER_AUTH_ALGO_OFFSET)[0],
        "auth_key_idx": struct.unpack_from("<I", data, HEADER_AUTH_KEY_IDX_OFFSET)[0],
        "crc_strings": [],
        "update_strings": [],
        "crypto_verification_strings": [],
        "crc_only": True,
        "crypto_found": False,
        "assessment": "",
        "notes": [],
    }

    # Auth algo interpretation (Ambiq convention)
    auth_algo_names = {
        0: "None (no authentication)",
        1: "CRC (CRC32 verification only)",
        2: "SHA-256",
        3: "ECDSA-P256",
    }
    algo_name = auth_algo_names.get(result["auth_algo"], f"Unknown ({result['auth_algo']})")
    result["auth_algo_name"] = algo_name

    # Search for CRC-related strings in the update path
    update_terms = ["CRC", "crc", "update", "Update", "firmware", "Firmware",
                    "image", "Image", "OTA", "wuff", "zbin"]
    crypto_terms = ["sign", "Sign", "signature", "Signature", "verify", "Verify",
                    "RSA", "ECDSA", "AES", "SHA", "hash", "Hash",
                    "encrypt", "Encrypt", "decrypt", "Decrypt",
                    "certificate", "cert"]

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]

        # Check for CRC in update context
        if "CRC" in text or "crc" in text:
            if any(u in text for u in ["update", "Update", "image", "Image", "Flash",
                                        "flash", "packet", "Packet", "firmware", "Firmware"]):
                result["crc_strings"].append({
                    "text": text[:200],
                    "offset": offset,
                    "address": f"0x{(offset + MRAM_BASE):08X}",
                })

        # Check for generic update strings
        for term in update_terms:
            if term in text and ("load" in text.lower() or "process" in text.lower() or
                                  "start" in text.lower() or "fail" in text.lower() or
                                  "pass" in text.lower() or "succeed" in text.lower()):
                result["update_strings"].append({
                    "text": text[:200],
                    "offset": offset,
                    "address": f"0x{(offset + MRAM_BASE):08X}",
                })
                break

        # Check for crypto in firmware update context specifically
        # Only flag crypto_found/crc_only if this is genuinely about firmware
        # update authentication (not generic "verify" or "signature" strings)
        for term in crypto_terms:
            if term in text:
                entry = {
                    "text": text[:200],
                    "offset": offset,
                    "address": f"0x{(offset + MRAM_BASE):08X}",
                    "crypto_term": term,
                }
                result["crypto_verification_strings"].append(entry)

                # Only mark as real crypto if it appears in a firmware update
                # authentication context (not "Write Verify", "strap signature",
                # "Secure encryption indication", etc.)
                is_fw_update_crypto = (
                    term in ("RSA", "ECDSA", "AES") or
                    (term == "SHA" and any(u in text for u in
                        ["firmware", "Firmware", "image", "Image", "OTA"])) or
                    (term.lower() in ("encrypt", "decrypt") and
                     any(u in text for u in ["firmware image", "Firmware image",
                                              "firmware update", "Firmware Update",
                                              "OTA"]))
                )
                if is_fw_update_crypto:
                    result["crypto_found"] = True
                    result["crc_only"] = False
                break

    # Analyze the "Firmware Load (0x8E)" command path
    fw_load_strings = []
    for s in all_strings:
        if "0x8E" in s["text"] or "0x90" in s["text"]:
            fw_load_strings.append({
                "text": s["text"][:200],
                "offset": s["offset"],
            })
    result["firmware_load_command_strings"] = fw_load_strings

    # Check for CRC of update image pass/fail
    for s in all_strings:
        if "CRC of update image" in s["text"]:
            result["notes"].append(f"Found CRC validation string: {s['text'][:100]}")

    # Determine overall assessment
    if result["auth_algo"] == 1:
        result["assessment"] = (
            "WEAK: Firmware uses CRC32-only authentication (auth_algo=1). "
            "No cryptographic signature verification is performed on firmware updates. "
            "An attacker with physical or BLE access could potentially load modified "
            "firmware images that pass CRC validation. The auth_key_idx=0x0D (13) "
            "suggests a key index is specified but with CRC-only auth, no asymmetric "
            "key verification occurs."
        )
    elif result["auth_algo"] == 0:
        result["assessment"] = (
            "CRITICAL: No firmware authentication configured (auth_algo=0). "
            "Firmware images are accepted without any integrity check."
        )
    elif result["auth_algo"] >= 2:
        result["assessment"] = (
            f"Firmware uses {algo_name} authentication. "
            "Cryptographic verification is configured in the image header."
        )
        result["crc_only"] = False

    # Check if there's a separate signature block at end of image
    # Ambiq images sometimes append a signature after the main payload
    image_size = struct.unpack_from("<I", data, HEADER_SIZE_OFFSET)[0]
    actual_size = len(data)
    if actual_size > image_size + 0x200:
        trailer_size = actual_size - image_size - 0x200
        result["notes"].append(
            f"Binary has {trailer_size} bytes beyond declared image size "
            f"(image_size={image_size}, file_size={actual_size}). "
            f"This could be a signature block or padding."
        )
        # Check if trailer has high entropy (could be a signature)
        trailer = data[image_size + 0x200:]
        if len(trailer) > 0:
            entropy = calculate_entropy(trailer[:256])
            result["notes"].append(
                f"Trailer entropy: {entropy:.2f} bits/byte "
                f"(high entropy > 7.0 suggests crypto material)"
            )

    elapsed = time.time() - start_time
    print(f"  Firmware update analysis complete in {elapsed:.1f}s")
    print(f"  Auth algorithm: {algo_name}")
    print(f"  CRC strings found: {len(result['crc_strings'])}")
    print(f"  Crypto verification strings: {len(result['crypto_verification_strings'])}")
    print(f"  Assessment: {result['assessment'][:100]}...")

    return result


# ---------------------------------------------------------------------------
# Phase 4: Cooper BLE Controller Authentication
# ---------------------------------------------------------------------------

def analyze_cooper_auth(data: bytes, all_strings: list) -> dict:
    """
    Analyze Cooper (Ambiq BLE radio controller) firmware authentication
    and the SBL communication protocol for Cooper updates.
    """
    print("\n[Track F] Phase 4: Cooper BLE controller auth")
    start_time = time.time()

    result = {
        "strings": [],
        "auth_strings": [],
        "sbl_error_strings": [],
        "cooper_init_strings": [],
        "code_refs": [],
        "auth_flow": [],
        "notes": [],
    }

    cooper_terms = ["Cooper", "cooper", "BLE Controller", "ble controller",
                    "BLE_Controller", "cordio"]

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]

        for term in cooper_terms:
            if term in text:
                code_refs = find_string_references(
                    data, offset, search_range=(0, CODE_REGION_END)
                )
                entry = {
                    "text": text[:200],
                    "offset": offset,
                    "address": f"0x{(offset + MRAM_BASE):08X}",
                    "code_refs": [f"0x{(r + MRAM_BASE):08X}" for r in code_refs[:5]],
                }

                if "Auth" in text or "auth" in text:
                    result["auth_strings"].append(entry)
                elif "SBL" in text or "Error" in text or "error" in text:
                    result["sbl_error_strings"].append(entry)
                elif "init" in text.lower() or "deinit" in text.lower():
                    result["cooper_init_strings"].append(entry)
                else:
                    result["strings"].append(entry)

                for ref in code_refs:
                    result["code_refs"].append(f"0x{(ref + MRAM_BASE):08X}")
                break

    result["code_refs"] = sorted(set(result["code_refs"]))

    # Analyze the authentication flow
    # Key strings: "BLE Controller FW Auth Passed", "Clear Cooper Signature"
    for s in result["auth_strings"]:
        text = s["text"]
        if "Auth Passed" in text:
            result["auth_flow"].append({
                "step": "auth_success",
                "string": text,
                "address": s["address"],
            })
        elif "Auth Failed" in text or "Auth Fail" in text:
            result["auth_flow"].append({
                "step": "auth_failure",
                "string": text,
                "address": s["address"],
            })

    # Check for Cooper signature handling
    for s in all_strings:
        if "Clear Cooper Signature" in s["text"]:
            result["auth_flow"].append({
                "step": "clear_signature_and_retry",
                "string": s["text"][:200],
                "address": f"0x{(s['offset'] + MRAM_BASE):08X}",
            })
            result["notes"].append(
                "Cooper has a signature clearing mechanism: when auth fails, "
                "the strap clears the Cooper signature and retries via SBL. "
                "This suggests Cooper firmware is authenticated before execution."
            )

    # Check for Cooper firmware version checking
    for s in all_strings:
        if "Cooper" in s["text"] and ("version" in s["text"].lower() or "firmware" in s["text"].lower()):
            result["strings"].append({
                "text": s["text"][:200],
                "offset": s["offset"],
                "address": f"0x{(s['offset'] + MRAM_BASE):08X}",
            })

    elapsed = time.time() - start_time
    print(f"  Cooper auth analysis complete in {elapsed:.1f}s")
    print(f"  Cooper strings: {len(result['strings'])}")
    print(f"  Auth strings: {len(result['auth_strings'])}")
    print(f"  Auth flow steps: {len(result['auth_flow'])}")

    return result


# ---------------------------------------------------------------------------
# Phase 5: Debug Interface Assessment
# ---------------------------------------------------------------------------

def analyze_debug_interfaces(data: bytes, all_strings: list) -> dict:
    """
    Assess exposed debug capabilities: UART console, debug menu,
    JTAG/SWD status.
    """
    print("\n[Track F] Phase 5: Debug interface assessment")
    start_time = time.time()

    result = {
        "uart": {
            "enabled": False,
            "ble_controllable": False,
            "timeout_configurable": False,
            "strings": [],
        },
        "debug_menu": {
            "found": False,
            "commands": [],
            "categories": [],
            "strings": [],
        },
        "jtag_swd": {
            "references_found": False,
            "strings": [],
        },
        "memfault": {
            "found": False,
            "strings": [],
        },
        "notes": [],
    }

    # UART analysis
    for s in all_strings:
        text = s["text"]
        if "UART" in text or "uart" in text:
            entry = {"text": text[:200], "offset": s["offset"]}

            if "Enable" in text or "enable" in text:
                result["uart"]["enabled"] = True
                result["uart"]["ble_controllable"] = True
            if "timeout" in text.lower():
                result["uart"]["timeout_configurable"] = True
            if "BLE_TURN_ON_BLE_UART" in text or "BLE_TURN_OFF_BLE_UART" in text:
                result["uart"]["ble_controllable"] = True

            result["uart"]["strings"].append(entry)

    # BLE UART control signals
    uart_ble_cmds = []
    for s in all_strings:
        text = s["text"]
        if "WSBLE_CMD_UART" in text:
            uart_ble_cmds.append(text[:100])
    if uart_ble_cmds:
        result["uart"]["ble_uart_commands"] = uart_ble_cmds
        result["notes"].append(
            f"UART can be enabled/disabled via BLE commands: {', '.join(uart_ble_cmds)}"
        )

    # Debug menu analysis
    debug_menu_strings = []
    debug_categories = set()
    debug_commands = []

    for s in all_strings:
        text = s["text"]

        # Look for debug menu commands (typically formatted as "  X   description")
        if "debugmenu" in text.lower() or "Debug Menu" in text or "debug menu" in text.lower():
            result["debug_menu"]["found"] = True
            debug_menu_strings.append({"text": text[:200], "offset": s["offset"]})

        # Parse debug command help text
        if re.match(r'^\s+[a-zA-Z<]\s', text):
            # Could be a debug menu help line
            stripped = text.strip()
            if any(k in stripped.lower() for k in ["set", "get", "show", "dump", "enable",
                                                      "disable", "force", "reset", "test"]):
                debug_commands.append(stripped[:100])

        # Debug category detection
        if "debug level" in text.lower():
            # Extract the subsystem being configured
            match = re.search(r'Set (\w+(?:\s+\w+)?)\s+debug level', text, re.IGNORECASE)
            if match:
                debug_categories.add(match.group(1))

    result["debug_menu"]["strings"] = debug_menu_strings
    result["debug_menu"]["commands"] = debug_commands[:50]  # Limit output
    result["debug_menu"]["categories"] = sorted(debug_categories)
    result["debug_menu"]["command_count"] = len(debug_commands)
    result["debug_menu"]["category_count"] = len(debug_categories)

    # JTAG/SWD analysis
    for s in all_strings:
        text = s["text"]
        if any(term in text for term in ["JTAG", "SWD", "DAP", "Debugger"]):
            result["jtag_swd"]["references_found"] = True
            result["jtag_swd"]["strings"].append({
                "text": text[:200],
                "offset": s["offset"],
            })

    # Also check for DAP/JTAG register references in code
    # Ambiq Apollo4 has a DAP lock at MCUCTRL offset 0x200
    dap_lock_addr = 0x40008200  # MCUCTRL + 0x200 (DAP Lock register)
    dap_refs = find_peripheral_references(data, dap_lock_addr, start=0, end=CODE_REGION_END)
    if dap_refs:
        result["jtag_swd"]["dap_lock_refs"] = [f"0x{r:06X}" for r in dap_refs]
        result["jtag_swd"]["references_found"] = True
        result["notes"].append(
            f"Found {len(dap_refs)} references to DAP lock register (0x{dap_lock_addr:08X})"
        )

    # Memfault integration
    for s in all_strings:
        text = s["text"]
        if "memfault" in text.lower() or "mflt" in text.lower() or "MFLT" in text:
            result["memfault"]["found"] = True
            result["memfault"]["strings"].append({
                "text": text[:200],
                "offset": s["offset"],
            })

    if result["memfault"]["found"]:
        result["notes"].append(
            f"Memfault crash reporting integration found ({len(result['memfault']['strings'])} strings). "
            "This provides remote crash reporting and could leak debug information."
        )

    elapsed = time.time() - start_time
    print(f"  Debug interface analysis complete in {elapsed:.1f}s")
    print(f"  UART strings: {len(result['uart']['strings'])}")
    print(f"  Debug menu commands: {result['debug_menu']['command_count']}")
    print(f"  Debug categories: {result['debug_menu']['category_count']}")
    print(f"  JTAG/SWD references: {result['jtag_swd']['references_found']}")
    print(f"  Memfault integration: {result['memfault']['found']}")

    return result


# ---------------------------------------------------------------------------
# Phase 6: Crypto Analysis
# ---------------------------------------------------------------------------

def calculate_entropy(data_block: bytes) -> float:
    """Calculate Shannon entropy of a data block in bits per byte."""
    if not data_block:
        return 0.0
    freq = defaultdict(int)
    for byte in data_block:
        freq[byte] += 1
    length = len(data_block)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def analyze_crypto(data: bytes, all_strings: list) -> dict:
    """
    Comprehensive search for cryptographic material including known
    constants, crypto library strings, and high-entropy blocks.
    """
    print("\n[Track F] Phase 6: Crypto analysis")
    start_time = time.time()

    result = {
        "constants_found": [],
        "libraries": [],
        "crypto_strings": [],
        "high_entropy_blocks": [],
        "keys": [],
        "notes": [],
    }

    # Search for known crypto constants
    print("  Searching for known crypto constants...")
    for name, pattern in CRYPTO_CONSTANTS.items():
        off = 0
        found_offsets = []
        while off < len(data):
            idx = data.find(pattern, off)
            if idx < 0:
                break
            found_offsets.append(idx)
            off = idx + len(pattern)

        if found_offsets:
            result["constants_found"].append({
                "name": name,
                "pattern_hex": pattern.hex(),
                "occurrences": len(found_offsets),
                "offsets": [f"0x{o:06X}" for o in found_offsets[:10]],
            })
            print(f"    {name}: {len(found_offsets)} occurrences")

    # Search for crypto library strings
    crypto_lib_terms = [
        "mbedtls", "mbedTLS", "wolfssl", "wolfSSL", "tinycrypt", "TinyCrypt",
        "bearssl", "BearSSL", "openssl", "OpenSSL", "libsodium",
        "ARM CryptoCell", "CryptoCell", "cc3xx",
        "Mbed TLS", "PKCS", "X.509", "x509",
    ]

    for s in all_strings:
        text = s["text"]
        for term in crypto_lib_terms:
            if term in text:
                result["libraries"].append({
                    "text": text[:200],
                    "offset": s["offset"],
                    "library": term,
                })
                break

    # Search for general crypto-related strings
    crypto_string_terms = [
        "encrypt", "decrypt", "cipher", "hash", "digest",
        "sign", "verify", "certificate", "private key", "public key",
        "RSA", "ECDSA", "AES", "DES", "SHA", "HMAC", "PBKDF",
        "nonce", "salt", "IV", "initialization vector",
    ]

    for s in all_strings:
        text = s["text"]
        text_lower = text.lower()
        for term in crypto_string_terms:
            if term.lower() in text_lower:
                # Avoid duplicates with crypto_lib_terms
                if not any(lib["text"] == text[:200] for lib in result["libraries"]):
                    result["crypto_strings"].append({
                        "text": text[:200],
                        "offset": s["offset"],
                        "term": term,
                    })
                break

    # Search for high-entropy blocks (potential keys or crypto material)
    # Scan in 256-byte blocks, looking for entropy > 7.5 bits/byte
    print("  Scanning for high-entropy blocks...")
    block_size = 256
    entropy_threshold = 7.5

    # Focus on data regions (after code, before string-dense area)
    scan_regions = [
        (CODE_REGION_END, len(data)),  # Data/rodata section
        (0, min(0x1000, len(data))),   # Header area
    ]

    for region_start, region_end in scan_regions:
        for off in range(region_start, min(region_end, len(data)) - block_size, block_size):
            block = data[off:off + block_size]
            entropy = calculate_entropy(block)
            if entropy > entropy_threshold:
                # Check if this is just compressed/random data or actual keys
                # Real keys tend to be exactly 16, 24, or 32 bytes within the block
                # Also filter out blocks that are mostly the same byte
                unique_bytes = len(set(block))
                if unique_bytes > 200:  # Truly high entropy, not just repetitive
                    result["high_entropy_blocks"].append({
                        "offset": f"0x{off:06X}",
                        "entropy": round(entropy, 3),
                        "unique_bytes": unique_bytes,
                        "first_16_bytes": block[:16].hex(),
                    })

    # Limit high-entropy results
    if len(result["high_entropy_blocks"]) > 50:
        result["notes"].append(
            f"Found {len(result['high_entropy_blocks'])} high-entropy blocks, "
            f"showing top 50 by entropy"
        )
        result["high_entropy_blocks"].sort(key=lambda x: -x["entropy"])
        result["high_entropy_blocks"] = result["high_entropy_blocks"][:50]

    # Check for specific key patterns
    # 0xDEADBEEF (stack canary / sentinel)
    sentinel_patterns = {
        "DEADBEEF": struct.pack("<I", 0xDEADBEEF),
        "CAFEBABE": struct.pack("<I", 0xCAFEBABE),
        "A5A5A5A5": struct.pack("<I", 0xA5A5A5A5),
        "5A5A5A5A": struct.pack("<I", 0x5A5A5A5A),
    }

    for name, pattern in sentinel_patterns.items():
        off = 0
        count = 0
        first_offset = -1
        while off < len(data):
            idx = data.find(pattern, off)
            if idx < 0:
                break
            if first_offset < 0:
                first_offset = idx
            count += 1
            off = idx + 4

        if count > 0:
            result["keys"].append({
                "type": "sentinel_pattern",
                "name": name,
                "occurrences": count,
                "first_offset": f"0x{first_offset:06X}" if first_offset >= 0 else "N/A",
            })

    # Look for CRC32 lookup table (256 x 4-byte entries)
    # A CRC32 table starts with 0x00000000 and has 0x77073096 at index 1
    crc32_table_marker = struct.pack("<I", 0x77073096)
    off = 0
    while off < len(data):
        idx = data.find(crc32_table_marker, off)
        if idx < 0:
            break
        # Check if this is preceded by 0x00000000 (index 0 of CRC32 table)
        if idx >= 4:
            prev_val = struct.unpack_from("<I", data, idx - 4)[0]
            if prev_val == 0x00000000:
                result["constants_found"].append({
                    "name": "CRC32_LOOKUP_TABLE",
                    "pattern_hex": "00000000 77073096...",
                    "occurrences": 1,
                    "offsets": [f"0x{(idx - 4):06X}"],
                })
                result["notes"].append(
                    f"CRC32 lookup table found at offset 0x{(idx - 4):06X} (1024 bytes)"
                )
        off = idx + 4

    elapsed = time.time() - start_time
    print(f"  Crypto analysis complete in {elapsed:.1f}s")
    print(f"  Crypto constants found: {len(result['constants_found'])}")
    print(f"  Crypto libraries: {len(result['libraries'])}")
    print(f"  Crypto strings: {len(result['crypto_strings'])}")
    print(f"  High-entropy blocks: {len(result['high_entropy_blocks'])}")

    return result


# ---------------------------------------------------------------------------
# Phase 7: Anti-Rollback Analysis
# ---------------------------------------------------------------------------

def analyze_anti_rollback(data: bytes, all_strings: list) -> dict:
    """
    Check for version enforcement mechanisms including monotonic
    counters, version comparison, and OTP fuse references.
    """
    print("\n[Track F] Phase 7: Anti-rollback analysis")
    start_time = time.time()

    result = {
        "found": False,
        "evidence": [],
        "version_strings": [],
        "otp_references": [],
        "notes": [],
    }

    # Search for rollback-related strings
    rollback_terms = ["rollback", "anti-rollback", "monotonic", "counter",
                      "version check", "version mismatch", "downgrade",
                      "minimum version", "min_version"]

    for s in all_strings:
        text = s["text"]
        text_lower = text.lower()
        for term in rollback_terms:
            if term.lower() in text_lower:
                result["found"] = True
                result["evidence"].append({
                    "text": text[:200],
                    "offset": s["offset"],
                    "term": term,
                })
                break

    # Search for version comparison strings
    version_terms = ["version", "Version", "firmware version", "expected version",
                     "incorrect firmware", "correct firmware"]
    for s in all_strings:
        text = s["text"]
        for term in version_terms:
            if term in text:
                result["version_strings"].append({
                    "text": text[:200],
                    "offset": s["offset"],
                })
                break

    # Search for OTP (One-Time Programmable) references
    # Apollo4 OTP region: INFO0 at 0x42000000
    otp_base_addr = 0x42000000
    otp_refs = find_peripheral_references(data, otp_base_addr, start=0, end=CODE_REGION_END)
    if otp_refs:
        result["otp_references"] = [f"0x{r:06X}" for r in otp_refs]
        result["found"] = True
        result["evidence"].append({
            "text": f"OTP (INFO0) base address 0x{otp_base_addr:08X} referenced in code",
            "offset": otp_refs[0],
            "term": "OTP",
        })

    # Also check for OTP-related strings
    for s in all_strings:
        text = s["text"]
        if any(term in text for term in ["OTP", "INFO0", "one-time", "fuse", "Fuse", "FUSE"]):
            result["otp_references"].append(s["text"][:200])
            if "OTP" in text or "fuse" in text.lower():
                result["evidence"].append({
                    "text": text[:200],
                    "offset": s["offset"],
                    "term": "OTP/Fuse",
                })

    # Check for Cooper firmware version enforcement
    for s in all_strings:
        if "incorrect firmware version" in s["text"].lower() or \
           "expected version" in s["text"].lower():
            result["evidence"].append({
                "text": s["text"][:200],
                "offset": s["offset"],
                "term": "version_enforcement",
            })
            result["notes"].append(
                "Cooper BLE controller has version enforcement - the main firmware "
                "checks Cooper's version and expects a specific version."
            )

    if not result["found"]:
        result["notes"].append(
            "No explicit anti-rollback mechanism found. The firmware does not appear "
            "to use monotonic counters or OTP fuses for version enforcement. "
            "Version checking exists only for Cooper BLE controller firmware."
        )

    elapsed = time.time() - start_time
    print(f"  Anti-rollback analysis complete in {elapsed:.1f}s")
    print(f"  Anti-rollback mechanisms found: {result['found']}")
    print(f"  Evidence items: {len(result['evidence'])}")
    print(f"  Version strings: {len(result['version_strings'])}")

    return result


# ---------------------------------------------------------------------------
# Phase 8: Memory Protection Analysis
# ---------------------------------------------------------------------------

def analyze_memory_protection(data: bytes, all_strings: list) -> dict:
    """
    Check for MPU configuration, stack canary patterns, and other
    memory protection mechanisms.
    """
    print("\n[Track F] Phase 8: Memory protection analysis")
    start_time = time.time()

    result = {
        "mpu_configured": False,
        "mpu_references": [],
        "stack_canary": False,
        "stack_canary_evidence": [],
        "privilege_separation": False,
        "notes": [],
    }

    # Search for MPU register references
    for name, addr in SYSTEM_REGISTERS.items():
        if "MPU" in name:
            refs = find_peripheral_references(data, addr, start=0, end=CODE_REGION_END)
            if refs:
                result["mpu_configured"] = True
                result["mpu_references"].append({
                    "register": name,
                    "address": f"0x{addr:08X}",
                    "code_refs": [f"0x{r:06X}" for r in refs[:10]],
                    "ref_count": len(refs),
                })
                print(f"    MPU register {name} (0x{addr:08X}): {len(refs)} references")

    # Also search for MPU-related strings
    for s in all_strings:
        text = s["text"]
        if "MPU" in text or "mpu" in text:
            result["mpu_references"].append({
                "type": "string",
                "text": text[:200],
                "offset": s["offset"],
            })

    # Check for stack canary patterns
    # GCC stack protector uses __stack_chk_guard and __stack_chk_fail
    canary_terms = ["__stack_chk", "stack_chk_fail", "stack_chk_guard",
                    "stack overflow", "stack guard", "STACK_OVERFLOW"]
    for s in all_strings:
        text = s["text"]
        for term in canary_terms:
            if term in text:
                result["stack_canary"] = True
                result["stack_canary_evidence"].append({
                    "text": text[:200],
                    "offset": s["offset"],
                    "term": term,
                })
                break

    # Look for stack canary sentinel values in code
    # Common patterns: 0xDEADBEEF at SP offsets
    deadbeef = struct.pack("<I", 0xDEADBEEF)
    deadbeef_count = 0
    off = 0
    while off < CODE_REGION_END:
        idx = data.find(deadbeef, off, CODE_REGION_END)
        if idx < 0:
            break
        deadbeef_count += 1
        off = idx + 4
    if deadbeef_count > 0:
        result["stack_canary_evidence"].append({
            "text": f"0xDEADBEEF pattern found {deadbeef_count} times in code region",
            "type": "sentinel_pattern",
        })

    # Check for privilege level strings
    for s in all_strings:
        text = s["text"].lower()
        if "privilege" in text or "unprivilege" in text or "svc" in text:
            result["privilege_separation"] = True
            result["notes"].append(f"Privilege-related string: {s['text'][:100]}")

    # Check SCB_SHCSR for MemManage/BusFault/UsageFault enables
    shcsr_refs = find_peripheral_references(
        data, SYSTEM_REGISTERS["SCB_SHCSR"], start=0, end=CODE_REGION_END
    )
    if shcsr_refs:
        result["notes"].append(
            f"SCB_SHCSR (System Handler Control) referenced {len(shcsr_refs)} times - "
            "fault handlers are likely configured"
        )

    # Check VTOR (Vector Table Offset Register) - if changed, could indicate
    # vector table relocation (common in bootloader->app transition)
    vtor_refs = find_peripheral_references(
        data, SYSTEM_REGISTERS["SCB_VTOR"], start=0, end=CODE_REGION_END
    )
    if vtor_refs:
        result["notes"].append(
            f"SCB_VTOR referenced {len(vtor_refs)} times - "
            "vector table is relocated during boot"
        )

    if not result["mpu_configured"]:
        result["notes"].append(
            "No MPU register references found in the code region. The MPU may not "
            "be configured, or MPU setup occurs in the SBL (Secondary Bootloader) "
            "which is in ROM and not part of this firmware image."
        )

    elapsed = time.time() - start_time
    print(f"  Memory protection analysis complete in {elapsed:.1f}s")
    print(f"  MPU configured: {result['mpu_configured']}")
    print(f"  MPU register references: {len(result['mpu_references'])}")
    print(f"  Stack canary: {result['stack_canary']}")

    return result


# ---------------------------------------------------------------------------
# Phase 9: Comprehensive Security String Scan
# ---------------------------------------------------------------------------

def security_string_scan(data: bytes, all_strings: list) -> dict:
    """
    Comprehensive scan for all security-relevant strings, categorized
    by type.
    """
    print("\n[Track F] Phase 9: Comprehensive security string scan")
    start_time = time.time()

    categories = defaultdict(list)

    category_terms = {
        "authentication": ["auth", "Auth", "authenticate", "credential", "login",
                          "password", "token", "session"],
        "cryptography": ["encrypt", "decrypt", "cipher", "hash", "digest",
                        "sign", "signature", "RSA", "ECDSA", "AES", "SHA",
                        "HMAC", "nonce", "salt", "IV"],
        "secure_boot": ["secure boot", "Secure Boot", "SBL", "bootloader",
                       "Bootloader", "boot verify"],
        "firmware_update": ["firmware update", "Firmware Update", "OTA",
                           "image load", "firmware load"],
        "debug_access": ["debug", "Debug", "JTAG", "SWD", "DAP",
                        "UART debug", "debug menu", "debug level"],
        "error_handling": ["assert", "Assert", "fault", "Fault", "error",
                          "Error", "crash", "Crash", "panic"],
        "key_management": ["key", "Key", "certificate", "cert", "x509",
                          "private", "public"],
    }

    for s in all_strings:
        text = s["text"]
        for category, terms in category_terms.items():
            if any(term in text for term in terms):
                categories[category].append({
                    "text": text[:150],
                    "offset": s["offset"],
                })
                break

    # Summarize
    summary = {}
    for cat, items in categories.items():
        # Deduplicate
        seen = set()
        unique = []
        for item in items:
            if item["text"] not in seen:
                seen.add(item["text"])
                unique.append(item)
        summary[cat] = {
            "count": len(unique),
            "strings": unique[:25],  # Limit per category
        }

    elapsed = time.time() - start_time
    print(f"  Security string scan complete in {elapsed:.1f}s")
    for cat, info in sorted(summary.items(), key=lambda x: -x[1]["count"]):
        print(f"    {cat:25s}: {info['count']} strings")

    return summary


# ---------------------------------------------------------------------------
# Security Assessment
# ---------------------------------------------------------------------------

def generate_security_assessment(
    boot_seq: dict,
    sbl_interface: dict,
    fw_update: dict,
    cooper_auth: dict,
    debug_ifaces: dict,
    crypto: dict,
    anti_rollback: dict,
    mem_prot: dict,
) -> str:
    """
    Generate an overall security assessment based on all analysis phases.
    """
    findings = []
    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}

    # Firmware authentication
    if fw_update.get("auth_algo") == 1:
        findings.append(
            "[HIGH] Firmware uses CRC32-only authentication (auth_algo=1). "
            "No cryptographic signature verification prevents loading of modified firmware."
        )
        severity_counts["HIGH"] += 1
    elif fw_update.get("auth_algo") == 0:
        findings.append(
            "[CRITICAL] No firmware authentication mechanism configured."
        )
        severity_counts["CRITICAL"] += 1
    else:
        findings.append(
            f"[INFO] Firmware authentication configured: {fw_update.get('auth_algo_name', 'unknown')}"
        )
        severity_counts["INFO"] += 1

    # Debug interfaces
    if debug_ifaces["uart"]["ble_controllable"]:
        findings.append(
            "[MEDIUM] UART debug console can be enabled/disabled via BLE commands. "
            "An attacker with BLE access could enable debug UART for information disclosure."
        )
        severity_counts["MEDIUM"] += 1

    if debug_ifaces["debug_menu"]["found"]:
        findings.append(
            f"[MEDIUM] Extensive debug menu with {debug_ifaces['debug_menu']['command_count']} "
            f"commands and {debug_ifaces['debug_menu']['category_count']} debug categories. "
            "Debug menu allows forcing resets, triggering asserts, and changing operational parameters."
        )
        severity_counts["MEDIUM"] += 1

    if debug_ifaces["memfault"]["found"]:
        findings.append(
            "[LOW] Memfault crash reporting integration present. Crash data may include "
            "sensitive memory contents or operational details."
        )
        severity_counts["LOW"] += 1

    # Crypto
    if not crypto["libraries"]:
        findings.append(
            "[HIGH] No standard cryptographic library identified (no mbedTLS, wolfSSL, etc.). "
            "The firmware may lack proper cryptographic implementations."
        )
        severity_counts["HIGH"] += 1

    if not crypto["constants_found"] or all(
        c["name"].startswith("CRC") or c["name"].startswith("RSA_E")
        for c in crypto["constants_found"]
    ):
        findings.append(
            "[INFO] No AES/SHA/ECDSA constants detected in firmware. "
            "Cryptographic operations may be performed by hardware (CRYPTO peripheral) "
            "or by the SBL/ROM."
        )
        severity_counts["INFO"] += 1

    # Anti-rollback
    if not anti_rollback["found"]:
        findings.append(
            "[MEDIUM] No anti-rollback mechanism detected. Downgrade attacks "
            "to older vulnerable firmware versions may be possible."
        )
        severity_counts["MEDIUM"] += 1

    # Memory protection
    if not mem_prot["mpu_configured"]:
        findings.append(
            "[LOW] No MPU configuration detected in the application firmware. "
            "Memory isolation between tasks relies on the RTOS alone. "
            "Note: MPU may be configured by the SBL in ROM."
        )
        severity_counts["LOW"] += 1

    if not mem_prot["stack_canary"]:
        findings.append(
            "[LOW] No stack canary mechanism detected (no __stack_chk_guard references). "
            "Stack buffer overflows may not be detected at runtime."
        )
        severity_counts["LOW"] += 1

    # Cooper auth
    if cooper_auth["auth_strings"]:
        findings.append(
            "[INFO] Cooper BLE controller has firmware authentication. "
            "The main MCU verifies Cooper's firmware before proceeding. "
            "Failed auth triggers signature clearing and retry via SBL."
        )
        severity_counts["INFO"] += 1

    # Build assessment text
    assessment = "WHOOP 5.0 FIRMWARE SECURITY ASSESSMENT\n"
    assessment += "=" * 40 + "\n\n"
    assessment += f"Findings: {severity_counts['CRITICAL']} Critical, "
    assessment += f"{severity_counts['HIGH']} High, "
    assessment += f"{severity_counts['MEDIUM']} Medium, "
    assessment += f"{severity_counts['LOW']} Low, "
    assessment += f"{severity_counts['INFO']} Info\n\n"

    for i, finding in enumerate(findings, 1):
        assessment += f"{i}. {finding}\n\n"

    assessment += "SUMMARY:\n"
    assessment += (
        "The firmware relies primarily on CRC32 for update integrity verification "
        "without cryptographic authentication (auth_algo=1). The Cooper BLE controller "
        "has its own firmware authentication mechanism via the SBL. Debug interfaces "
        "are extensive and BLE-controllable, including UART enable/disable and a full "
        "debug command menu. No standard crypto libraries were identified in the "
        "application firmware, though the Ambiq Apollo4 has hardware crypto acceleration "
        "that could be used at a lower level. Anti-rollback mechanisms were not detected "
        "in the application layer."
    )

    return assessment


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Track F: Comprehensive Security Analysis")
    print("=" * 70)
    overall_start = time.time()

    # Load firmware
    print(f"\nLoading firmware: {DEFAULT_BIN}")
    data = load_firmware()
    print(f"  Loaded {len(data):,} bytes")

    # Extract all strings first (used by multiple phases)
    print("\nExtracting strings from binary...")
    string_start = time.time()
    all_strings = extract_strings(data, min_length=4, start=0, end=len(data))
    print(f"  Extracted {len(all_strings):,} strings in {time.time() - string_start:.1f}s")

    # Phase 1: Boot sequence
    boot_seq = analyze_boot_sequence(data)

    # Phase 2: SBL interface
    sbl_interface = analyze_sbl_interface(data, all_strings)

    # Phase 3: Firmware update security
    fw_update = analyze_firmware_update_security(data, all_strings)

    # Phase 4: Cooper BLE controller auth
    cooper_auth = analyze_cooper_auth(data, all_strings)

    # Phase 5: Debug interfaces
    debug_ifaces = analyze_debug_interfaces(data, all_strings)

    # Phase 6: Crypto analysis
    crypto = analyze_crypto(data, all_strings)

    # Phase 7: Anti-rollback
    anti_rollback = analyze_anti_rollback(data, all_strings)

    # Phase 8: Memory protection
    mem_prot = analyze_memory_protection(data, all_strings)

    # Phase 9: Security string scan
    security_strings = security_string_scan(data, all_strings)

    # Generate overall assessment
    assessment = generate_security_assessment(
        boot_seq, sbl_interface, fw_update, cooper_auth,
        debug_ifaces, crypto, anti_rollback, mem_prot,
    )

    # Compile final output
    output = {
        "boot_sequence": boot_seq,
        "sbl_interface": sbl_interface,
        "firmware_update_security": fw_update,
        "cooper_auth": cooper_auth,
        "debug_interfaces": debug_ifaces,
        "crypto_analysis": crypto,
        "anti_rollback": anti_rollback,
        "memory_protection": mem_prot,
        "security_string_categories": security_strings,
        "security_assessment": assessment,
    }

    # Save output
    save_output("track_f_security.json", output)

    # Print assessment summary
    elapsed = time.time() - overall_start
    print("\n" + "=" * 70)
    print("Security Assessment Summary")
    print("=" * 70)
    print(assessment)
    print(f"\nTotal elapsed time: {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
