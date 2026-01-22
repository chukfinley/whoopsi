#!/usr/bin/env python3
"""
Track B: Comprehensive String Analysis
=======================================

Extracts and categorizes all ASCII strings from the Whoop 5.0 firmware,
reconstructs the source project structure from embedded file paths,
finds cross-references from code to strings, and identifies format
strings and log messages.

Usage:
    python3 track_b_strings.py

Output:
    analysis/output/track_b_strings.json
"""

import json
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
    extract_strings,
    categorize_string,
    find_string_references,
    find_function_start,
    save_output,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Minimum string length for extraction
MIN_STRING_LENGTH = 4

# Code region boundary (file offsets)
CODE_REGION_END = 0x0A0000

# String-dense regions
STRING_REGION_PRIMARY_START = 0x0B0000
STRING_REGION_PRIMARY_END = 0x0D0000

# Maximum number of cross-references to compute (for performance)
MAX_XREF_STRINGS = 2000

# Format string pattern
FORMAT_STRING_RE = re.compile(r'%[-+0 #]*\d*\.?\d*[diouxXeEfFgGaAcspnl%]')


# ---------------------------------------------------------------------------
# Full Binary String Extraction
# ---------------------------------------------------------------------------

def extract_all_strings(data: bytes) -> list:
    """
    Extract ALL ASCII strings from the entire binary, with emphasis on
    the known string-dense region.
    """
    print("[Track B] Phase 1: Full binary string extraction")
    start_time = time.time()

    all_strings = extract_strings(data, min_length=MIN_STRING_LENGTH, start=0, end=len(data))

    elapsed = time.time() - start_time
    print(f"  Extracted {len(all_strings):,} strings in {elapsed:.1f}s")
    print(f"  String region breakdown:")

    # Count strings by region
    regions = {
        "code_region (0x000000-0x0A0000)": (0, CODE_REGION_END),
        "transition (0x0A0000-0x0B0000)": (CODE_REGION_END, STRING_REGION_PRIMARY_START),
        "primary_strings (0x0B0000-0x0D0000)": (STRING_REGION_PRIMARY_START, STRING_REGION_PRIMARY_END),
        "post_strings (0x0D0000-end)": (STRING_REGION_PRIMARY_END, len(data)),
    }

    for name, (start, end) in regions.items():
        count = sum(1 for s in all_strings if start <= s["offset"] < end)
        print(f"    {name}: {count:,} strings")

    # Add MRAM addresses to each string
    for s in all_strings:
        s["mram_address"] = f"0x{MRAM_BASE + s['offset']:08X}"

    return all_strings


# ---------------------------------------------------------------------------
# String Categorization
# ---------------------------------------------------------------------------

def categorize_all_strings(all_strings: list) -> dict:
    """
    Categorize every string using the common.categorize_string function
    and group by category.
    """
    print("\n[Track B] Phase 2: String categorization")

    strings_by_category = defaultdict(list)
    category_counts = defaultdict(int)

    for s in all_strings:
        cat = categorize_string(s["text"])
        s["category"] = cat
        strings_by_category[cat].append({
            "offset": s["offset"],
            "mram_address": s["mram_address"],
            "text": s["text"],
            "length": s["length"],
        })
        category_counts[cat] += 1

    # Sort categories by count (descending)
    sorted_counts = dict(sorted(category_counts.items(), key=lambda x: -x[1]))

    print("  Category counts:")
    for cat, count in sorted_counts.items():
        print(f"    {cat:20s}: {count:5d}")

    return {
        "strings_by_category": dict(strings_by_category),
        "category_counts": sorted_counts,
    }


# ---------------------------------------------------------------------------
# Source Path Extraction & Project Structure Reconstruction
# ---------------------------------------------------------------------------

def extract_source_paths(all_strings: list) -> dict:
    """
    Find embedded source file paths (./src/*.c, ./modules/*.c, etc.)
    and reconstruct the original project directory tree.
    """
    print("\n[Track B] Phase 3: Source path extraction & project structure")

    # Patterns that match source file references
    path_patterns = [
        re.compile(r'\./[a-zA-Z_][a-zA-Z_0-9/]*\.[ch]'),     # ./src/foo.c
        re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*/[a-zA-Z_][a-zA-Z_0-9/]*\.[ch]'),  # modules/foo/bar.c
    ]

    # Standalone file pattern (stricter: must look like a real filename)
    standalone_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z_0-9]*\.[ch]$')

    source_paths = []
    seen_paths = set()

    for s in all_strings:
        text = s["text"].strip()

        # Skip strings from the code region that are likely false positives
        # Real source paths typically appear in the string-dense regions
        if s["offset"] < CODE_REGION_END:
            # Only accept paths from code region if they clearly look like paths
            if not text.startswith("./") and "/" not in text:
                continue

        for pattern in path_patterns:
            match = pattern.search(text)
            if match:
                path = match.group(0)
                # Validate: path components should be reasonable identifiers
                parts = path.replace("./", "").split("/")
                valid = all(
                    re.match(r'^[a-zA-Z_][a-zA-Z_0-9.-]*$', p)
                    for p in parts if p
                )
                if valid and path not in seen_paths:
                    seen_paths.add(path)
                    source_paths.append({
                        "path": path,
                        "string_offset": s["offset"],
                        "mram_address": s["mram_address"],
                        "full_string": text,
                    })
                break
        else:
            # Check standalone .c/.h files only if in string region
            if s["offset"] >= CODE_REGION_END:
                match = standalone_pattern.match(text)
                if match and text not in seen_paths:
                    seen_paths.add(text)
                    source_paths.append({
                        "path": text,
                        "string_offset": s["offset"],
                        "mram_address": s["mram_address"],
                        "full_string": text,
                    })

    # Also check for strings that are specifically tagged as SOURCE_PATH
    for s in all_strings:
        if s.get("category") == "SOURCE_PATH" and s["text"] not in seen_paths:
            text = s["text"].strip()
            # Must contain a / or end with .c/.h, and have valid path characters
            if ("/" in text or text.endswith(".c") or text.endswith(".h")):
                # Validate that the path looks reasonable (not garbled)
                clean = text.lstrip("./")
                parts = clean.split("/")
                valid = all(
                    re.match(r'^[a-zA-Z_][a-zA-Z_0-9.-]*$', p)
                    for p in parts if p
                )
                if valid:
                    seen_paths.add(text)
                    source_paths.append({
                        "path": text,
                        "string_offset": s["offset"],
                        "mram_address": s["mram_address"],
                        "full_string": text,
                    })

    print(f"  Found {len(source_paths)} unique source paths")

    # Reconstruct project directory structure
    project_structure = {}

    for sp in source_paths:
        path = sp["path"]
        # Normalize path
        if path.startswith("./"):
            path = path[2:]

        parts = path.split("/")
        current = project_structure

        # Build nested dict structure
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                # Leaf file
                if "__files__" not in current:
                    current["__files__"] = []
                if part not in current["__files__"]:
                    current["__files__"].append(part)
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]

    # Pretty-print the structure
    def print_tree(d, indent=0):
        prefix = "    " + "  " * indent
        files = d.get("__files__", [])
        dirs = {k: v for k, v in d.items() if k != "__files__"}
        for dirname in sorted(dirs.keys()):
            print(f"{prefix}{dirname}/")
            print_tree(dirs[dirname], indent + 1)
        for fname in sorted(files):
            print(f"{prefix}{fname}")

    print("  Reconstructed project structure:")
    print_tree(project_structure)

    return {
        "source_paths": source_paths,
        "project_structure": project_structure,
    }


# ---------------------------------------------------------------------------
# Format String Detection
# ---------------------------------------------------------------------------

def _is_likely_genuine_string(text: str) -> bool:
    """
    Heuristic to filter out random byte sequences that happen to contain
    printable characters. Genuine firmware strings tend to have:
    - Mostly alphanumeric + common punctuation
    - Reasonable ratio of spaces/letters
    - Recognizable words or patterns
    """
    if len(text) < 4:
        return False

    # Count character classes
    alpha_count = sum(1 for c in text if c.isalpha())
    digit_count = sum(1 for c in text if c.isdigit())
    space_count = sum(1 for c in text if c == ' ')
    punct_count = sum(1 for c in text if c in '.,;:!?()-_=+/\\[]{}@#$&*\'\"<>')
    control_like = sum(1 for c in text if ord(c) < 0x20)

    total = len(text)
    readable_ratio = (alpha_count + digit_count + space_count + punct_count) / total

    # A genuine string should be mostly readable characters
    if readable_ratio < 0.7:
        return False

    # Strings with at least some alphabetic content are more likely genuine
    if alpha_count < 2 and len(text) > 6:
        return False

    # Very short strings with mostly non-alpha are probably code artifacts
    if len(text) <= 6 and alpha_count < len(text) // 2:
        return False

    return True


def find_format_strings(all_strings: list) -> list:
    """
    Identify strings containing printf-style format specifiers (%d, %s, etc.)
    and classify them as log messages, debug messages, or format templates.

    Filters out false positives from code regions where random bytes happen
    to contain '%' followed by a format character.
    """
    print("\n[Track B] Phase 4: Format string detection")

    format_strings = []

    for s in all_strings:
        text = s["text"]

        # Filter out likely non-string artifacts, especially from the code region
        if s["offset"] < CODE_REGION_END and not _is_likely_genuine_string(text):
            continue

        matches = FORMAT_STRING_RE.findall(text)
        if not matches:
            continue

        # Additional validation: the string around the format specifier should
        # look like a real message, not random bytes
        # Skip if the format specifier is the ONLY readable content
        non_fmt_text = FORMAT_STRING_RE.sub('', text).strip()
        if len(non_fmt_text) < 2 and len(text) < 10:
            continue

        # Classify the format string
        fmt_type = "format_template"
        lower_text = text.lower()
        if any(kw in lower_text for kw in ["error", "err", "fail", "fault"]):
            fmt_type = "error_message"
        elif any(kw in lower_text for kw in ["warn", "warning"]):
            fmt_type = "warning_message"
        elif any(kw in lower_text for kw in ["debug", "dbg", "trace"]):
            fmt_type = "debug_message"
        elif any(kw in lower_text for kw in ["info", "log", "print"]):
            fmt_type = "info_message"
        elif ":" in text and ("%d" in text or "%u" in text or "%x" in text
                              or "%lu" in text or "%s" in text):
            fmt_type = "log_message"

        format_strings.append({
            "offset": s["offset"],
            "mram_address": s["mram_address"],
            "text": text,
            "format_specifiers": matches,
            "num_specifiers": len(matches),
            "format_type": fmt_type,
            "category": s.get("category", "OTHER"),
        })

    # Count by type
    type_counts = defaultdict(int)
    for fs in format_strings:
        type_counts[fs["format_type"]] += 1

    print(f"  Found {len(format_strings)} format strings")
    print("  Format string types:")
    for fmt_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {fmt_type:20s}: {count:5d}")

    # Show some interesting examples (prefer log/error messages)
    interesting = [fs for fs in format_strings if fs["format_type"] != "format_template"]
    if not interesting:
        interesting = format_strings
    print("  Examples:")
    for fs in interesting[:8]:
        text_preview = fs["text"][:70] + ("..." if len(fs["text"]) > 70 else "")
        print(f'    [{fs["format_type"]:15s}] {text_preview}')

    return format_strings


# ---------------------------------------------------------------------------
# Cross-Reference Analysis
# ---------------------------------------------------------------------------

def find_cross_references(data: bytes, all_strings: list) -> list:
    """
    Find cross-references from code to strings using two methods:
    1. Literal pool scan: find 4-byte aligned values in code that match string MRAM addresses
    2. LDR [PC, #imm] resolution: decode LDR instructions, follow to literal pool, resolve address

    This combined approach catches both direct pool references and
    instruction-level references.
    """
    print("\n[Track B] Phase 5: Cross-reference analysis")
    start_time = time.time()

    code_end = min(CODE_REGION_END, len(data))

    # Build fast lookup: string file offset -> string record
    string_by_offset = {}
    for s in all_strings:
        string_by_offset[s["offset"]] = s

    string_offsets = set(string_by_offset.keys())

    # -----------------------------------------------------------
    # Method 1: Scan literal pool entries (4-byte aligned values)
    # -----------------------------------------------------------
    print("  Method 1: Scanning literal pool entries...")
    # Map from string file offset -> list of code offsets referencing it
    string_refs = defaultdict(list)

    for off in range(0, code_end - 3, 4):
        val = struct.unpack_from("<I", data, off)[0]
        if MRAM_BASE <= val < MRAM_BASE + len(data):
            file_off = val - MRAM_BASE
            if file_off in string_offsets:
                string_refs[file_off].append(("pool", off))

    pool_refs_count = sum(len(v) for v in string_refs.values())
    print(f"  Literal pool references: {pool_refs_count} "
          f"({len(string_refs)} unique strings)")

    # -----------------------------------------------------------
    # Method 2: Decode LDR Rd, [PC, #imm] instructions
    # -----------------------------------------------------------
    print("  Method 2: Resolving LDR [PC, #imm] instructions...")
    ldr_refs_count = 0

    for off in range(0, code_end - 1, 2):
        hw = struct.unpack_from("<H", data, off)[0]

        # 16-bit LDR Rd, [PC, #imm8*4]: encoding 0x4800-0x4FFF
        if (hw & 0xF800) == 0x4800:
            imm8 = hw & 0xFF
            pc = (off + 4) & ~3  # PC is instruction address + 4, word-aligned
            pool_addr = pc + imm8 * 4
            if pool_addr + 3 < len(data):
                val = struct.unpack_from("<I", data, pool_addr)[0]
                if MRAM_BASE <= val < MRAM_BASE + len(data):
                    file_off = val - MRAM_BASE
                    if file_off in string_offsets:
                        # Record this as an LDR-resolved reference
                        string_refs[file_off].append(("ldr", off))
                        ldr_refs_count += 1

    print(f"  LDR-resolved references: {ldr_refs_count}")

    # -----------------------------------------------------------
    # Build cross-reference output
    # -----------------------------------------------------------
    print("  Building cross-reference records...")

    cross_references = []
    for str_off, refs in string_refs.items():
        s = string_by_offset[str_off]

        ref_details = []
        for ref_type, ref_off in refs[:10]:  # Limit per string
            func_start = find_function_start(data, ref_off)
            ref_detail = {
                "type": ref_type,
                "code_offset": f"0x{ref_off:06X}",
                "code_mram": f"0x{MRAM_BASE + ref_off:08X}",
            }
            if func_start is not None:
                ref_detail["containing_function_offset"] = f"0x{func_start:06X}"
                ref_detail["containing_function_mram"] = f"0x{MRAM_BASE + func_start:08X}"
            ref_details.append(ref_detail)

        cross_references.append({
            "string": s["text"][:200],
            "offset": s["offset"],
            "mram_address": s.get("mram_address", f"0x{MRAM_BASE + s['offset']:08X}"),
            "category": s.get("category", "OTHER"),
            "num_code_refs": len(refs),
            "code_refs": ref_details,
        })

    elapsed = time.time() - start_time
    total_refs = sum(xr["num_code_refs"] for xr in cross_references)

    print(f"  Cross-reference scan completed in {elapsed:.1f}s")
    print(f"  Strings with code references: {len(cross_references)}")
    print(f"  Total code references found:  {total_refs}")

    # Sort by number of references (most referenced first)
    cross_references.sort(key=lambda x: -x["num_code_refs"])

    # Show top referenced strings
    print("  Top 15 most-referenced strings:")
    for xr in cross_references[:15]:
        text_preview = xr["string"][:50] + ("..." if len(xr["string"]) > 50 else "")
        print(f'    {xr["num_code_refs"]:3d} refs: [{xr["category"]:10s}] {text_preview}')

    return cross_references


# ---------------------------------------------------------------------------
# Additional Analysis: BLE Command Strings
# ---------------------------------------------------------------------------

def analyze_ble_strings(strings_by_category: dict) -> dict:
    """
    Deeper analysis of BLE-related strings to identify protocol commands,
    service/characteristic names, and connection state strings.
    """
    print("\n[Track B] Phase 6: BLE string deep analysis")

    ble_strings = strings_by_category.get("BLE", [])
    if not ble_strings:
        print("  No BLE strings found")
        return {}

    ble_analysis = {
        "service_uuids": [],
        "characteristic_names": [],
        "connection_states": [],
        "protocol_commands": [],
        "advertising_strings": [],
        "other_ble": [],
    }

    uuid_pattern = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

    for s in ble_strings:
        text = s["text"]
        lower = text.lower()

        if uuid_pattern.search(text):
            ble_analysis["service_uuids"].append(text)
        elif any(kw in lower for kw in ["connect", "disconnect", "bonded", "paired"]):
            ble_analysis["connection_states"].append(text)
        elif any(kw in lower for kw in ["advert", "scan"]):
            ble_analysis["advertising_strings"].append(text)
        elif any(kw in lower for kw in ["cmd", "command", "request", "response", "notify"]):
            ble_analysis["protocol_commands"].append(text)
        elif any(kw in lower for kw in ["characteristic", "service", "gatt"]):
            ble_analysis["characteristic_names"].append(text)
        else:
            ble_analysis["other_ble"].append(text)

    for key, items in ble_analysis.items():
        if items:
            print(f"  {key}: {len(items)}")

    return ble_analysis


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start_time = time.time()
    print("=" * 70)
    print("Track B: Comprehensive String Analysis")
    print("=" * 70)
    print(f"Binary: {DEFAULT_BIN}")

    # Verify binary exists
    if not os.path.isfile(DEFAULT_BIN):
        print(f"ERROR: Binary not found at {DEFAULT_BIN}")
        sys.exit(1)

    bin_size = os.path.getsize(DEFAULT_BIN)
    print(f"Size:   {bin_size:,} bytes")
    print()

    # Load firmware
    data = load_firmware()
    print(f"Loaded {len(data):,} bytes\n")

    # Phase 1: Extract all strings
    all_strings = extract_all_strings(data)

    # Phase 2: Categorize strings
    cat_result = categorize_all_strings(all_strings)

    # Phase 3: Source paths & project structure
    src_result = extract_source_paths(all_strings)

    # Phase 4: Format strings
    format_strings = find_format_strings(all_strings)

    # Phase 5: Cross-references
    cross_references = find_cross_references(data, all_strings)

    # Phase 6: BLE string deep analysis
    ble_analysis = analyze_ble_strings(cat_result["strings_by_category"])

    # ---------------------------------------------------------------------------
    # Assemble final output
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Assembling output...")

    output = {
        "metadata": {
            "binary": DEFAULT_BIN,
            "binary_size": bin_size,
            "base_address": f"0x{MRAM_BASE:08X}",
            "analysis_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(time.time() - start_time, 1),
            "min_string_length": MIN_STRING_LENGTH,
        },
        "total_strings": len(all_strings),
        "strings_by_category": cat_result["strings_by_category"],
        "category_counts": cat_result["category_counts"],
        "source_paths": src_result["source_paths"],
        "project_structure": src_result["project_structure"],
        "format_strings": format_strings,
        "cross_references": cross_references,
        "ble_analysis": ble_analysis,
    }

    # Save JSON output
    json_path = save_output("track_b_strings.json", output)

    # Final summary
    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print("Track B Summary")
    print("=" * 70)
    print(f"  Total strings extracted:     {output['total_strings']:,}")
    print(f"  Categories:                  {len(cat_result['category_counts'])}")
    for cat, count in cat_result["category_counts"].items():
        print(f"    {cat:20s}: {count:5d}")
    print(f"  Source paths found:          {len(src_result['source_paths'])}")
    print(f"  Format strings:              {len(format_strings)}")
    print(f"  Strings with code xrefs:     {len(cross_references)}")
    total_xrefs = sum(xr["num_code_refs"] for xr in cross_references)
    print(f"  Total code cross-references: {total_xrefs}")
    print(f"  Elapsed time:                {elapsed:.1f}s")
    print()
    print(f"  Output: {json_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
