#!/usr/bin/env python3
"""
Track D: Health Algorithm Extraction
=====================================
Scans Whoop 5.0 firmware (Apollo4 Blue Plus / ARM Cortex-M4F) for:
  1. FPU instruction regions (math-heavy algorithm code)
  2. Heart rate calculation functions
  3. SpO2 algorithm (red/IR ratio, lookup tables)
  4. HRV / RMSSD computation
  5. Motion detection / IMU processing
  6. Algorithm function isolation with pseudocode

Usage:
    python3 track_d_algorithms.py
    python3 track_d_algorithms.py /path/to/firmware.bin

Output:
    analysis/output/track_d_algorithms.json
"""

import struct
import sys
import re
from pathlib import Path
from collections import defaultdict

# Ensure the analysis package is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))
from analysis.common import (
    load_firmware,
    get_capstone_md,
    disasm,
    disasm_function,
    find_function_start,
    find_function_prologs,
    find_string_references,
    find_bl_targets,
    extract_strings,
    MRAM_BASE,
    save_output,
    DEFAULT_BIN,
)


# FPU mnemonics we look for (ARM VFPv4 / Cortex-M4F)
FPU_MNEMONICS = {
    # Arithmetic
    "vadd", "vsub", "vmul", "vdiv", "vnmul", "vneg", "vabs",
    "vmla", "vmls", "vnmla", "vnmls", "vfma", "vfms", "vfnma", "vfnms",
    "vsqrt",
    # Conversion
    "vcvt", "vcvtb", "vcvtt",
    # Compare
    "vcmp", "vcmpe",
    # Move
    "vmov", "vmrs", "vmsr",
    # Load / Store
    "vldr", "vstr", "vldm", "vstm", "vpush", "vpop",
}

# Minimum FPU instructions to consider a region "algorithm-like"
MIN_FPU_CLUSTER = 5
# Maximum gap (in bytes) between FPU instructions to merge into one region
MAX_FPU_GAP = 128

# Heuristic upper bound for code region. Firmware strings start heavily
# around 0x0A0000, but interleaved code+data extends further.  We scan
# up to 0x100000 to catch algorithm code that sits beyond the main
# application text section.
CODE_REGION_END = 0x100000


# ---------------------------------------------------------------------------
# Enhanced string reference finder (same as track_c)
# ---------------------------------------------------------------------------

def _find_all_string_refs(data: bytes, string_offset: int) -> list:
    """Find code references to a string using literal pools across the full binary."""
    refs = find_string_references(data, string_offset,
                                  search_range=(0, len(data)))
    return refs


# ---------------------------------------------------------------------------
# 1. FPU instruction region scanner
# ---------------------------------------------------------------------------

def scan_fpu_regions(data: bytes) -> list:
    """Scan entire code region for clusters of floating-point instructions."""
    print("[1/6] Scanning for FPU instruction regions...")
    md = get_capstone_md()
    code_end = min(len(data), CODE_REGION_END)

    # First pass: find all FPU instruction offsets
    fpu_offsets = []
    chunk_size = 0x10000  # Disassemble in 64KB chunks to manage memory
    for chunk_start in range(0, code_end, chunk_size):
        chunk_end = min(chunk_start + chunk_size, code_end)
        try:
            instrs = list(md.disasm(data[chunk_start:chunk_end], chunk_start))
            for ins in instrs:
                mnemonic_base = ins.mnemonic.split(".")[0].lower()
                if mnemonic_base in FPU_MNEMONICS:
                    fpu_offsets.append({
                        "offset": ins.address,
                        "mnemonic": ins.mnemonic,
                        "op_str": ins.op_str,
                    })
        except Exception as e:
            print(f"  Warning: disasm error at 0x{chunk_start:06X}: {e}")
            continue

    print(f"  Total FPU instructions found: {len(fpu_offsets)}")

    if not fpu_offsets:
        return []

    # Second pass: cluster FPU instructions into regions
    regions = []
    current_start = fpu_offsets[0]["offset"]
    current_end = fpu_offsets[0]["offset"]
    current_instrs = [fpu_offsets[0]]

    for fpu in fpu_offsets[1:]:
        if fpu["offset"] - current_end <= MAX_FPU_GAP:
            current_end = fpu["offset"]
            current_instrs.append(fpu)
        else:
            if len(current_instrs) >= MIN_FPU_CLUSTER:
                regions.append({
                    "start": current_start,
                    "end": current_end,
                    "fpu_count": len(current_instrs),
                    "size_bytes": current_end - current_start,
                })
            current_start = fpu["offset"]
            current_end = fpu["offset"]
            current_instrs = [fpu]

    # Flush last region
    if len(current_instrs) >= MIN_FPU_CLUSTER:
        regions.append({
            "start": current_start,
            "end": current_end,
            "fpu_count": len(current_instrs),
            "size_bytes": current_end - current_start,
        })

    # Compute density
    for r in regions:
        r["density"] = round(r["fpu_count"] / max(r["size_bytes"], 1), 4)

    regions.sort(key=lambda r: r["fpu_count"], reverse=True)

    print(f"  FPU regions (>= {MIN_FPU_CLUSTER} FPU insns): {len(regions)}")
    if regions:
        top = regions[0]
        print(f"  Densest region: 0x{top['start']:06X}-0x{top['end']:06X} "
              f"({top['fpu_count']} FPU insns in {top['size_bytes']} bytes)")

    # Add instruction detail to top regions
    for r in regions[:30]:
        try:
            region_len = min(r["size_bytes"] + 64, 4096)
            instrs = list(md.disasm(
                data[r["start"]:r["start"] + region_len], r["start"]
            ))
            fpu_insns = []
            for ins in instrs:
                mnemonic_base = ins.mnemonic.split(".")[0].lower()
                if mnemonic_base in FPU_MNEMONICS:
                    fpu_insns.append(
                        f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                    )
            r["instructions"] = fpu_insns[:30]
        except Exception:
            r["instructions"] = []

    return regions


# ---------------------------------------------------------------------------
# 2. Heart rate calculation functions
# ---------------------------------------------------------------------------

def find_hr_algorithm(data: bytes, strings: list, fpu_regions: list) -> dict:
    """Find heart rate calculation functions."""
    print("[2/6] Searching for heart rate algorithm...")
    md = get_capstone_md()

    hr_keywords = [
        "heart", "hr", "bpm", "rr_interval", "rr_ms", "heartbeat",
        "pulse", "beat_detect", "peak_detect",
    ]

    # Find HR-related strings
    hr_strings = []
    hr_func_addrs = set()
    hr_functions = []

    for s in strings:
        text_lower = s["text"].lower()
        matched = [kw for kw in hr_keywords if kw in text_lower]
        if matched:
            hr_strings.append({
                "text": s["text"][:150],
                "offset": s["offset"],
                "keywords": matched,
            })

            # Find code referencing this string (full binary scan)
            code_refs = _find_all_string_refs(data, s["offset"])
            for cr in code_refs[:5]:
                func_start = find_function_start(data, cr)
                if func_start is not None and func_start not in hr_func_addrs:
                    hr_func_addrs.add(func_start)

    # Also look for functions in the densest FPU regions that sit near
    # known HR strings (within 8KB)
    hr_string_offsets = {s["offset"] for s in hr_strings}
    for region in fpu_regions[:10]:
        for s_off in hr_string_offsets:
            if abs(region["start"] - s_off) < 0x2000:
                func_start = find_function_start(data, region["start"])
                if func_start is not None:
                    hr_func_addrs.add(func_start)

    # Disassemble discovered HR functions
    for func_addr in sorted(hr_func_addrs):
        try:
            instrs = disasm_function(data, func_addr, max_insns=80, md=md)
            insn_text = [
                f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                for ins in instrs[:30]
            ]

            fpu_count = sum(
                1 for ins in instrs
                if ins.mnemonic.split(".")[0].lower() in FPU_MNEMONICS
            )

            has_division = any(
                ins.mnemonic.startswith("vdiv") or ins.mnemonic == "sdiv"
                or ins.mnemonic == "udiv"
                for ins in instrs
            )

            hr_functions.append({
                "address": func_addr,
                "fpu_count": fpu_count,
                "has_division": has_division,
                "instruction_count": len(instrs),
                "disassembly": insn_text,
            })
        except Exception:
            pass

    # Search for 60000 (ms-per-minute) constant in literal pools
    bpm_constant_refs = []
    for val in [60000, 60000.0]:
        if isinstance(val, float):
            pattern = struct.pack("<f", val)
        else:
            pattern = struct.pack("<I", val)
        off = 0
        scan_end = min(len(data), CODE_REGION_END)
        while off < scan_end:
            idx = data.find(pattern, off, scan_end)
            if idx < 0:
                break
            bpm_constant_refs.append({
                "offset": idx,
                "value": str(val),
                "type": "float" if isinstance(val, float) else "int",
            })
            off = idx + 4

    # Search for 0xEA60 in disassembled instructions of top FPU regions
    for r in fpu_regions[:20]:
        try:
            region_len = min(r["size_bytes"] + 64, 4096)
            instrs = list(md.disasm(
                data[r["start"]:r["start"] + region_len], r["start"]
            ))
            for ins in instrs:
                if "0xea60" in ins.op_str.lower() or "60000" in ins.op_str:
                    bpm_constant_refs.append({
                        "offset": ins.address,
                        "value": "0xEA60 in instruction",
                        "instruction": f"{ins.mnemonic} {ins.op_str}",
                    })
        except Exception:
            pass

    # Generate pseudocode for the most FPU-heavy HR function
    pseudocode = ""
    hr_functions.sort(key=lambda f: f["fpu_count"], reverse=True)
    if hr_functions:
        top_fn = hr_functions[0]
        pseudocode = _generate_pseudocode(data, top_fn["address"], md,
                                          "hr_calculate")

    print(f"  HR-related strings: {len(hr_strings)}")
    print(f"  HR functions found: {len(hr_functions)}")
    print(f"  BPM constant (60000) refs: {len(bpm_constant_refs)}")

    return {
        "functions": hr_functions[:20],
        "string_refs": hr_strings[:30],
        "bpm_constant_refs": bpm_constant_refs[:20],
        "pseudocode": pseudocode,
    }


# ---------------------------------------------------------------------------
# 3. SpO2 algorithm
# ---------------------------------------------------------------------------

def find_spo2_algorithm(data: bytes, strings: list, fpu_regions: list) -> dict:
    """Find SpO2 (blood oxygen saturation) calculation code."""
    print("[3/6] Searching for SpO2 algorithm...")
    md = get_capstone_md()

    spo2_keywords = [
        "spo2", "oxygen", "saturation", "red", "infrared",
        "ratio", "r_value", "calibration",
        "sigproc_spo2", "spo2_run", "spo2_calc",
    ]

    spo2_strings = []
    spo2_func_addrs = set()
    spo2_functions = []

    for s in strings:
        text_lower = s["text"].lower()
        matched = [kw for kw in spo2_keywords if kw in text_lower]
        if not matched:
            continue
        # Filter out false positives: "red" inside words like "configured"
        if matched == ["red"] and not re.search(r'\bred\b', text_lower):
            continue

        spo2_strings.append({
            "text": s["text"][:150],
            "offset": s["offset"],
            "keywords": matched,
        })

        code_refs = _find_all_string_refs(data, s["offset"])
        for cr in code_refs[:5]:
            func_start = find_function_start(data, cr)
            if func_start is not None:
                spo2_func_addrs.add(func_start)

    for func_addr in sorted(spo2_func_addrs):
        try:
            instrs = disasm_function(data, func_addr, max_insns=100, md=md)
            insn_text = [
                f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                for ins in instrs[:30]
            ]

            fpu_count = sum(
                1 for ins in instrs
                if ins.mnemonic.split(".")[0].lower() in FPU_MNEMONICS
            )

            # Detect ratio calculation pattern: vdiv after vmul
            has_ratio = False
            for i, ins in enumerate(instrs):
                if ins.mnemonic.startswith("vdiv"):
                    for j in range(max(0, i - 10), i):
                        if instrs[j].mnemonic.startswith("vmul"):
                            has_ratio = True
                            break
                if has_ratio:
                    break

            # Detect lookup table access (LDR with scaled index)
            has_lut = any(
                ins.mnemonic in ("ldr", "ldr.w", "ldrb", "ldrh")
                and ("lsl" in ins.op_str or ", r" in ins.op_str)
                for ins in instrs
            )

            spo2_functions.append({
                "address": func_addr,
                "fpu_count": fpu_count,
                "has_ratio_pattern": has_ratio,
                "has_lookup_table": has_lut,
                "instruction_count": len(instrs),
                "disassembly": insn_text,
            })
        except Exception:
            pass

    # Generate pseudocode for most interesting SpO2 function
    pseudocode = ""
    spo2_functions.sort(key=lambda f: f["fpu_count"], reverse=True)
    if spo2_functions:
        pseudocode = _generate_pseudocode(data, spo2_functions[0]["address"],
                                          md, "spo2_calculate")

    print(f"  SpO2-related strings: {len(spo2_strings)}")
    print(f"  SpO2 functions found: {len(spo2_functions)}")

    return {
        "functions": spo2_functions[:20],
        "string_refs": spo2_strings[:30],
        "pseudocode": pseudocode,
    }


# ---------------------------------------------------------------------------
# 4. HRV (RMSSD) computation
# ---------------------------------------------------------------------------

def find_hrv_algorithm(data: bytes, strings: list, fpu_regions: list) -> dict:
    """Find HRV / RMSSD computation code by locating VSQRT instructions."""
    print("[4/6] Searching for HRV/RMSSD algorithm...")
    md = get_capstone_md()
    code_end = min(len(data), CODE_REGION_END)

    # Find all VSQRT instructions
    vsqrt_locations = []
    chunk_size = 0x10000
    for chunk_start in range(0, code_end, chunk_size):
        chunk_end = min(chunk_start + chunk_size, code_end)
        try:
            instrs = list(md.disasm(
                data[chunk_start:chunk_end], chunk_start
            ))
            for ins in instrs:
                if ins.mnemonic.lower().startswith("vsqrt"):
                    func_start = find_function_start(data, ins.address)
                    vsqrt_locations.append({
                        "offset": ins.address,
                        "instruction": f"{ins.mnemonic} {ins.op_str}",
                        "function_start": func_start,
                    })
        except Exception:
            continue

    print(f"  VSQRT instructions found: {len(vsqrt_locations)}")

    # HRV-related strings
    hrv_keywords = [
        "hrv", "rmssd", "sdnn", "pnn50", "variability",
        "successive", "interval", "rr",
    ]

    hrv_strings = []
    hrv_func_addrs_from_strings = set()
    for s in strings:
        text_lower = s["text"].lower()
        matched = [kw for kw in hrv_keywords if kw in text_lower]
        if matched:
            hrv_strings.append({
                "text": s["text"][:150],
                "offset": s["offset"],
                "keywords": matched,
            })
            # Try to find code references
            code_refs = _find_all_string_refs(data, s["offset"])
            for cr in code_refs[:3]:
                func_start = find_function_start(data, cr)
                if func_start is not None:
                    hrv_func_addrs_from_strings.add(func_start)

    # Analyze functions containing VSQRT
    hrv_functions = []
    seen_funcs = set()

    for vsqrt in vsqrt_locations:
        func_addr = vsqrt["function_start"]
        if func_addr is None or func_addr in seen_funcs:
            continue
        seen_funcs.add(func_addr)

        try:
            instrs = disasm_function(data, func_addr, max_insns=120, md=md)
            insn_text = [
                f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                for ins in instrs[:30]
            ]

            fpu_stats = defaultdict(int)
            for ins in instrs:
                mnemonic_base = ins.mnemonic.split(".")[0].lower()
                if mnemonic_base in FPU_MNEMONICS:
                    fpu_stats[mnemonic_base] += 1

            has_sub = fpu_stats.get("vsub", 0) > 0
            has_mul = fpu_stats.get("vmul", 0) > 0 or fpu_stats.get("vmla", 0) > 0
            has_add = fpu_stats.get("vadd", 0) > 0
            has_div = fpu_stats.get("vdiv", 0) > 0
            has_sqrt = fpu_stats.get("vsqrt", 0) > 0

            rmssd_score = sum([has_sub, has_mul, has_add, has_div, has_sqrt])

            has_loop = any(
                ins.mnemonic in ("b", "bne", "bne.w", "blt", "blt.w",
                                 "bgt", "bgt.w", "ble", "ble.w", "bge",
                                 "bhs", "blo", "bcc", "bcs")
                and ins.op_str.startswith("#")
                and _parse_branch_target(ins) is not None
                and _parse_branch_target(ins) < ins.address
                for ins in instrs
            )

            hrv_functions.append({
                "address": func_addr,
                "vsqrt_at": vsqrt["offset"],
                "rmssd_pattern_score": rmssd_score,
                "fpu_stats": dict(fpu_stats),
                "has_loop": has_loop,
                "instruction_count": len(instrs),
                "disassembly": insn_text,
            })
        except Exception:
            pass

    # Also add functions found via HRV strings that were not in VSQRT set
    for func_addr in sorted(hrv_func_addrs_from_strings):
        if func_addr in seen_funcs:
            continue
        seen_funcs.add(func_addr)
        try:
            instrs = disasm_function(data, func_addr, max_insns=80, md=md)
            insn_text = [
                f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                for ins in instrs[:30]
            ]
            fpu_stats = defaultdict(int)
            for ins in instrs:
                mnemonic_base = ins.mnemonic.split(".")[0].lower()
                if mnemonic_base in FPU_MNEMONICS:
                    fpu_stats[mnemonic_base] += 1

            hrv_functions.append({
                "address": func_addr,
                "vsqrt_at": None,
                "rmssd_pattern_score": 0,
                "fpu_stats": dict(fpu_stats),
                "has_loop": False,
                "instruction_count": len(instrs),
                "disassembly": insn_text,
                "source": "string_ref",
            })
        except Exception:
            pass

    hrv_functions.sort(key=lambda f: f["rmssd_pattern_score"], reverse=True)

    # Generate pseudocode for best candidate
    pseudocode = ""
    if hrv_functions:
        pseudocode = _generate_pseudocode(data, hrv_functions[0]["address"],
                                          md, "hrv_rmssd_calculate")

    print(f"  HRV-related strings: {len(hrv_strings)}")
    print(f"  Functions with VSQRT: {sum(1 for f in hrv_functions if f.get('vsqrt_at'))}")
    print(f"  Total HRV candidate functions: {len(hrv_functions)}")
    if hrv_functions and hrv_functions[0]["rmssd_pattern_score"] > 0:
        best = hrv_functions[0]
        print(f"  Best RMSSD candidate: 0x{best['address']:06X} "
              f"(score {best['rmssd_pattern_score']}/5)")

    return {
        "functions": hrv_functions[:20],
        "string_refs": hrv_strings[:20],
        "vsqrt_locations": vsqrt_locations[:50],
        "pseudocode": pseudocode,
    }


# ---------------------------------------------------------------------------
# 5. Motion detection / IMU processing
# ---------------------------------------------------------------------------

def find_motion_detection(data: bytes, strings: list, fpu_regions: list) -> dict:
    """Find motion detection and IMU data processing code."""
    print("[5/6] Searching for motion detection / IMU processing...")
    md = get_capstone_md()

    motion_keywords = [
        "motion", "accel", "gyro", "imu", "activity",
        "step", "movement", "orientation", "magnitude",
        "sleep", "wake", "still", "active",
        "icm", "accelerometer", "gravity",
    ]

    motion_strings = []
    motion_func_addrs = set()
    motion_functions = []

    for s in strings:
        text_lower = s["text"].lower()
        matched = [kw for kw in motion_keywords if kw in text_lower]
        if matched:
            motion_strings.append({
                "text": s["text"][:150],
                "offset": s["offset"],
                "keywords": matched,
            })

            code_refs = _find_all_string_refs(data, s["offset"])
            for cr in code_refs[:5]:
                func_start = find_function_start(data, cr)
                if func_start is not None:
                    motion_func_addrs.add(func_start)

    for func_addr in sorted(motion_func_addrs):
        try:
            instrs = disasm_function(data, func_addr, max_insns=80, md=md)
            insn_text = [
                f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                for ins in instrs[:30]
            ]

            fpu_count = sum(
                1 for ins in instrs
                if ins.mnemonic.split(".")[0].lower() in FPU_MNEMONICS
            )

            has_vmul = any(ins.mnemonic.startswith("vmul") for ins in instrs)
            has_vadd = any(ins.mnemonic.startswith("vadd") for ins in instrs)
            has_vsqrt = any(ins.mnemonic.startswith("vsqrt") for ins in instrs)
            magnitude_pattern = has_vmul and has_vadd and has_vsqrt

            motion_functions.append({
                "address": func_addr,
                "fpu_count": fpu_count,
                "has_magnitude_pattern": magnitude_pattern,
                "instruction_count": len(instrs),
                "disassembly": insn_text,
            })
        except Exception:
            pass

    motion_functions.sort(key=lambda f: f["fpu_count"], reverse=True)

    print(f"  Motion-related strings: {len(motion_strings)}")
    print(f"  Motion functions found: {len(motion_functions)}")
    magnitude_funcs = sum(
        1 for f in motion_functions if f["has_magnitude_pattern"]
    )
    print(f"  Functions with magnitude pattern (sqrt(x^2+y^2+z^2)): "
          f"{magnitude_funcs}")

    return {
        "functions": motion_functions[:20],
        "string_refs": motion_strings[:30],
    }


# ---------------------------------------------------------------------------
# 6. Algorithm function isolation with pseudocode
# ---------------------------------------------------------------------------

def isolate_algorithm_functions(
    data: bytes,
    fpu_regions: list,
    hr_algo: dict,
    spo2_algo: dict,
    hrv_algo: dict,
    motion_algo: dict,
) -> list:
    """For the top algorithm regions, produce detailed disassembly + pseudocode."""
    print("[6/6] Isolating algorithm functions with pseudocode...")
    md = get_capstone_md()

    isolated = []

    # Gather all known algorithm function addresses with labels
    algo_funcs = {}
    for fn in hr_algo.get("functions", []):
        algo_funcs[fn["address"]] = "hr"
    for fn in spo2_algo.get("functions", []):
        algo_funcs[fn["address"]] = "spo2"
    for fn in hrv_algo.get("functions", []):
        algo_funcs[fn["address"]] = "hrv"
    for fn in motion_algo.get("functions", []):
        algo_funcs[fn["address"]] = "motion"

    # Also look at the top FPU regions that are NOT yet classified
    for region in fpu_regions[:15]:
        func_addr = find_function_start(data, region["start"])
        if func_addr is not None and func_addr not in algo_funcs:
            algo_funcs[func_addr] = "unknown_fpu_heavy"

    for func_addr, category in sorted(algo_funcs.items()):
        try:
            instrs = disasm_function(data, func_addr, max_insns=150, md=md)
            if len(instrs) < 3:
                continue

            fpu_count = sum(
                1 for ins in instrs
                if ins.mnemonic.split(".")[0].lower() in FPU_MNEMONICS
            )

            pseudocode = _generate_pseudocode(data, func_addr, md,
                                              f"func_{func_addr:06X}")

            isolated.append({
                "address": func_addr,
                "category": category,
                "instruction_count": len(instrs),
                "fpu_instruction_count": fpu_count,
                "pseudocode": pseudocode,
            })
        except Exception:
            pass

    isolated.sort(key=lambda f: f["fpu_instruction_count"], reverse=True)
    isolated = isolated[:40]

    print(f"  Isolated algorithm functions: {len(isolated)}")
    categories = defaultdict(int)
    for f in isolated:
        categories[f["category"]] += 1
    for cat, count in sorted(categories.items()):
        print(f"    {cat}: {count}")

    return isolated


# ---------------------------------------------------------------------------
# Pseudocode generator
# ---------------------------------------------------------------------------

def _generate_pseudocode(data: bytes, func_addr: int, md, func_name: str) -> str:
    """Generate Python-like pseudocode from disassembled ARM Thumb-2 instructions."""
    try:
        instrs = disasm_function(data, func_addr, max_insns=150, md=md)
    except Exception:
        return f"# Could not disassemble function at 0x{func_addr:06X}"

    if not instrs:
        return f"# Empty function at 0x{func_addr:06X}"

    lines = [
        f"def {func_name}():  # 0x{func_addr:06X}",
        f"    # {len(instrs)} instructions",
    ]

    # Track register usage for variable naming
    float_regs_used = set()
    int_regs_used = set()
    branch_targets = set()
    bl_targets = []

    for ins in instrs:
        mnemonic = ins.mnemonic.lower()
        op = ins.op_str

        if mnemonic.startswith("v") and ("s" in op or "d" in op):
            for reg in re.findall(r'[sd]\d+', op):
                float_regs_used.add(reg)
        for reg in re.findall(r'r\d+', op):
            int_regs_used.add(reg)

        if mnemonic in ("b", "b.w", "bne", "bne.w", "beq", "beq.w",
                         "blt", "blt.w", "bgt", "bgt.w", "ble", "ble.w",
                         "bge", "bge.w", "bhs", "blo", "bcc", "bcs"):
            target = _parse_branch_target(ins)
            if target is not None:
                branch_targets.add(target)

        if mnemonic in ("bl", "bl.w"):
            target = _parse_branch_target(ins)
            if target is not None:
                bl_targets.append(target)

    if float_regs_used:
        sorted_fregs = sorted(float_regs_used,
                              key=lambda r: int(r[1:]))
        lines.append(f"    # Float regs: {', '.join(sorted_fregs)}")
    if int_regs_used:
        sorted_iregs = sorted(int_regs_used,
                              key=lambda r: int(r[1:]))
        lines.append(f"    # Int regs: {', '.join(sorted_iregs)}")
    if bl_targets:
        unique_bl = sorted(set(bl_targets))
        lines.append(f"    # Calls: {', '.join(f'0x{t:06X}' for t in unique_bl[:8])}")
    lines.append("")

    indent = "    "
    label_counter = 0

    for ins in instrs:
        addr = ins.address
        mnemonic = ins.mnemonic.lower()
        op = ins.op_str

        if addr in branch_targets:
            lines.append(f"  label_{label_counter}:  # 0x{addr:06X}")
            label_counter += 1

        pseudo = _insn_to_pseudo(mnemonic, op, addr)
        if pseudo:
            lines.append(f"{indent}{pseudo}")

    return "\n".join(lines)


def _insn_to_pseudo(mnemonic: str, op: str, addr: int) -> str:
    """Convert a single ARM instruction to pseudocode."""
    m = mnemonic.lower()
    parts = [p.strip() for p in op.split(",")]

    # FPU arithmetic
    if m.startswith("vadd"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} + {parts[2]}  # float add"
        elif len(parts) == 2:
            return f"{parts[0]} += {parts[1]}  # float add"
    if m.startswith("vsub"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} - {parts[2]}  # float sub"
        elif len(parts) == 2:
            return f"{parts[0]} -= {parts[1]}  # float sub"
    if m.startswith("vmul"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} * {parts[2]}  # float mul"
        elif len(parts) == 2:
            return f"{parts[0]} *= {parts[1]}  # float mul"
    if m.startswith("vdiv"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} / {parts[2]}  # float div"
    if m.startswith("vsqrt"):
        if len(parts) >= 2:
            return f"{parts[0]} = sqrt({parts[1]})  # RMSSD / magnitude?"
    if m.startswith("vabs"):
        if len(parts) >= 2:
            return f"{parts[0]} = abs({parts[1]})"
    if m.startswith("vneg"):
        if len(parts) >= 2:
            return f"{parts[0]} = -{parts[1]}"
    if m.startswith("vmla"):
        if len(parts) >= 3:
            return f"{parts[0]} += {parts[1]} * {parts[2]}  # multiply-accumulate"
    if m.startswith("vmls"):
        if len(parts) >= 3:
            return f"{parts[0]} -= {parts[1]} * {parts[2]}  # multiply-subtract"
    if m.startswith("vfma"):
        if len(parts) >= 3:
            return f"{parts[0]} = fma({parts[0]}, {parts[1]}, {parts[2]})  # fused multiply-add"
    if m.startswith("vcmp"):
        if len(parts) >= 2:
            return f"flags = compare({parts[0]}, {parts[1]})  # float compare"
        elif len(parts) == 1:
            return f"flags = compare({parts[0]}, 0.0)  # float compare vs zero"
    if m.startswith("vcvt"):
        if len(parts) >= 2:
            return f"{parts[0]} = convert({parts[1]})  # type convert"
    if m.startswith("vmov"):
        if len(parts) >= 2:
            return f"{parts[0]} = {parts[1]}  # float move"
    if m.startswith("vmrs"):
        return f"apsr = fpscr  # copy FP flags to CPU flags"
    if m.startswith("vldr"):
        if len(parts) >= 2:
            return f"{parts[0]} = load_float({parts[1]})  # float load"
    if m.startswith("vstr"):
        if len(parts) >= 2:
            return f"store_float({parts[1]}, {parts[0]})  # float store"
    if m.startswith("vpush"):
        return f"push_float({op})  # save float regs"
    if m.startswith("vpop"):
        return f"pop_float({op})  # restore float regs"

    # Integer arithmetic
    if m in ("add", "add.w", "adds", "adds.w"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} + {parts[2]}"
        elif len(parts) == 2:
            return f"{parts[0]} += {parts[1]}"
    if m in ("sub", "sub.w", "subs", "subs.w"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} - {parts[2]}"
        elif len(parts) == 2:
            return f"{parts[0]} -= {parts[1]}"
    if m in ("mul", "muls"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} * {parts[2]}"
    if m in ("sdiv", "udiv"):
        if len(parts) >= 3:
            kind = "signed" if m == "sdiv" else "unsigned"
            return f"{parts[0]} = {parts[1]} / {parts[2]}  # {kind} divide"
    if m in ("mla",):
        if len(parts) >= 4:
            return f"{parts[0]} = {parts[1]} * {parts[2]} + {parts[3]}"

    # Memory
    if m in ("ldr", "ldr.w", "ldrb", "ldrb.w", "ldrh", "ldrh.w",
             "ldrsb", "ldrsh"):
        width = {"ldrb": "byte", "ldrb.w": "byte", "ldrh": "half",
                 "ldrh.w": "half", "ldrsb": "sbyte", "ldrsh": "shalf"
                 }.get(m, "word")
        if len(parts) >= 2:
            return f"{parts[0]} = load_{width}({', '.join(parts[1:])})"
    if m in ("str", "str.w", "strb", "strb.w", "strh", "strh.w"):
        width = {"strb": "byte", "strb.w": "byte", "strh": "half",
                 "strh.w": "half"}.get(m, "word")
        if len(parts) >= 2:
            return f"store_{width}({', '.join(parts[1:])}, {parts[0]})"

    # Control flow
    if m in ("push", "push.w"):
        return f"push({op})"
    if m in ("pop", "pop.w"):
        return f"pop({op})  # return" if "pc" in op else f"pop({op})"
    if m == "bx" and "lr" in op:
        return "return"
    if m in ("bl", "bl.w"):
        target = _parse_branch_target_from_op(op)
        if target is not None:
            return f"call(0x{target:06X})"
        return f"call({op})"
    if m in ("b", "b.w"):
        return f"goto {op}"
    if m.startswith("b") and m not in ("bl", "bl.w", "bx", "bic", "bfc",
                                        "bfi"):
        cond = m.replace(".w", "").replace("b", "", 1)
        return f"if {cond}: goto {op}"

    # Comparison
    if m in ("cmp", "cmp.w"):
        if len(parts) >= 2:
            return f"compare({parts[0]}, {parts[1]})"
    if m in ("tst", "tst.w"):
        if len(parts) >= 2:
            return f"test({parts[0]} & {parts[1]})"

    # Bitwise / Move
    if m in ("mov", "mov.w", "movs", "movs.w"):
        if len(parts) >= 2:
            return f"{parts[0]} = {parts[1]}"
    if m in ("movw",):
        if len(parts) >= 2:
            return f"{parts[0]} = {parts[1]}  # low 16 bits"
    if m in ("movt",):
        if len(parts) >= 2:
            return f"{parts[0]} |= ({parts[1]} << 16)  # high 16 bits"
    if m in ("and", "and.w", "ands", "ands.w"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} & {parts[2]}"
    if m in ("orr", "orr.w", "orrs"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} | {parts[2]}"
    if m in ("eor", "eor.w"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} ^ {parts[2]}"
    if m in ("lsl", "lsl.w", "lsls"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} << {parts[2]}"
    if m in ("lsr", "lsr.w", "lsrs"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} >> {parts[2]}"
    if m in ("asr", "asr.w"):
        if len(parts) >= 3:
            return f"{parts[0]} = {parts[1]} >> {parts[2]}  # arithmetic"

    # NOP / IT blocks / CBZ/CBNZ
    if m == "nop":
        return "# nop"
    if m.startswith("it"):
        return f"# IT block: {m} {op}"
    if m == "cbz":
        if len(parts) >= 2:
            return f"if {parts[0]} == 0: goto {parts[1]}"
    if m == "cbnz":
        if len(parts) >= 2:
            return f"if {parts[0]} != 0: goto {parts[1]}"

    # Fallback
    return f"# {m} {op}"


def _parse_branch_target(ins) -> int | None:
    """Parse branch target address from a capstone instruction."""
    return _parse_branch_target_from_op(ins.op_str)


def _parse_branch_target_from_op(op_str: str) -> int | None:
    """Parse branch target address from operand string."""
    op = op_str.strip()
    if op.startswith("#"):
        op = op[1:]
    if op.startswith("0x"):
        try:
            return int(op, 16)
        except ValueError:
            return None
    try:
        return int(op)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Track D: Health Algorithm Extraction")
    print("=" * 70)

    bin_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BIN
    print(f"Firmware: {bin_path}")

    try:
        data = load_firmware(bin_path)
    except FileNotFoundError:
        print(f"ERROR: Firmware not found at {bin_path}")
        sys.exit(1)

    print(f"Size: {len(data):,} bytes")
    print()

    print("Extracting strings...")
    strings = extract_strings(data, min_length=6)
    print(f"  Found {len(strings)} strings")
    print()

    # 1. FPU region scan
    fpu_regions = scan_fpu_regions(data)
    print()

    # 2. Heart rate algorithm
    hr_algo = find_hr_algorithm(data, strings, fpu_regions)
    print()

    # 3. SpO2 algorithm
    spo2_algo = find_spo2_algorithm(data, strings, fpu_regions)
    print()

    # 4. HRV / RMSSD
    hrv_algo = find_hrv_algorithm(data, strings, fpu_regions)
    print()

    # 5. Motion detection
    motion_algo = find_motion_detection(data, strings, fpu_regions)
    print()

    # 6. Algorithm function isolation
    isolated = isolate_algorithm_functions(
        data, fpu_regions, hr_algo, spo2_algo, hrv_algo, motion_algo
    )
    print()

    # Build summary
    total_fpu_insns = sum(r["fpu_count"] for r in fpu_regions)
    summary = {
        "total_fpu_regions": len(fpu_regions),
        "total_fpu_instructions": total_fpu_insns,
        "hr_functions": len(hr_algo.get("functions", [])),
        "hr_strings": len(hr_algo.get("string_refs", [])),
        "spo2_functions": len(spo2_algo.get("functions", [])),
        "spo2_strings": len(spo2_algo.get("string_refs", [])),
        "hrv_vsqrt_locations": len(hrv_algo.get("vsqrt_locations", [])),
        "hrv_functions": len(hrv_algo.get("functions", [])),
        "motion_functions": len(motion_algo.get("functions", [])),
        "isolated_algorithm_functions": len(isolated),
    }

    output = {
        "fpu_regions": fpu_regions[:50],
        "hr_algorithm": hr_algo,
        "spo2_algorithm": spo2_algo,
        "hrv_algorithm": hrv_algo,
        "motion_detection": motion_algo,
        "isolated_functions": isolated,
        "algorithm_summary": summary,
    }

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print()

    save_output("track_d_algorithms.json", output)
    print()
    print("Done.")


if __name__ == "__main__":
    main()
