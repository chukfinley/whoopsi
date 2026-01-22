#!/usr/bin/env python3
"""
Track A: Comprehensive ARM Disassembly & Function Mapping
=========================================================

Analyzes the Whoop 5.0 firmware (ARM Cortex-M4F Thumb-2, Ambiq Apollo4)
using radare2, capstone, and angr to produce a complete function map,
call graph, vector table, and function classification.

Usage:
    python3 track_a_disassembly.py

Output:
    analysis/output/track_a_functions.json
    analysis/output/track_a_callgraph.dot
"""

import json
import os
import struct
import sys
import time
import signal
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Add parent to path so we can import common
sys.path.insert(0, str(Path(__file__).parent))

from common import (
    DEFAULT_BIN,
    MRAM_BASE,
    PERIPH_BASE,
    VECTOR_TABLE_OFFSET,
    APOLLO4_PERIPHERALS,
    OUTPUT_DIR,
    load_firmware,
    get_capstone_md,
    disasm_function,
    find_function_prologs,
    find_bl_targets,
    parse_vector_table,
    extract_strings,
    categorize_string,
    find_string_references,
    save_output,
    R2Wrapper,
    get_angr_project,
    get_angr_cfg,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Code region is approximately 0x0 to 0xA0000 (in file offsets)
CODE_REGION_END = 0x0A0000

# String-dense region for reference scanning
STRING_REGION_START = 0x0B0000
STRING_REGION_END = 0x0D0000

# Timeout for radare2 analysis (seconds)
R2_ANALYSIS_TIMEOUT = 300

# Timeout for angr CFGFast (seconds)
ANGR_CFG_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Radare2 Analysis
# ---------------------------------------------------------------------------

def run_r2_analysis(bin_path: str) -> dict:
    """
    Run radare2 auto-analysis and extract functions, cross-references,
    and call graph data.

    Returns a dict with r2_functions, callgraph_edges, callgraph_dot.
    """
    print("[Track A] Phase 1: Radare2 analysis")
    result = {
        "r2_functions": [],
        "callgraph_edges": [],
        "callgraph_dot": "",
    }

    try:
        with R2Wrapper(bin_path) as r2:
            # Run auto-analysis with timeout protection
            start_time = time.time()
            print(f"  Starting aaa analysis (timeout: {R2_ANALYSIS_TIMEOUT}s)...")

            # Use 'aaa' for thorough analysis. If it takes too long, the
            # wrapper will block, so we rely on the r2pipe timeout or
            # perform lighter analysis as fallback.
            try:
                r2.analyze("aaa")
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"  WARNING: aaa failed after {elapsed:.0f}s: {e}")
                print("  Falling back to lighter 'aa' analysis...")
                try:
                    r2.r2.cmd("aa")
                except Exception as e2:
                    print(f"  WARNING: aa also failed: {e2}")
                    return result

            elapsed = time.time() - start_time
            print(f"  Analysis completed in {elapsed:.1f}s")

            # Extract all detected functions
            print("  Extracting functions (aflj)...")
            functions_raw = r2.get_functions()
            if functions_raw:
                for fn in functions_raw:
                    result["r2_functions"].append({
                        "name": fn.get("name", "unknown"),
                        "offset": fn.get("offset", 0),
                        "size": fn.get("size", 0),
                        "nargs": fn.get("nargs", 0),
                        "nlocals": fn.get("nlocals", 0),
                        "nbbs": fn.get("nbbs", 0),
                        "type": fn.get("type", ""),
                        "callrefs": fn.get("callrefs", []),
                        "codexrefs": fn.get("codexrefs", []),
                    })
                print(f"  Found {len(result['r2_functions'])} functions via r2")
            else:
                print("  WARNING: No functions returned from r2 aflj")

            # Extract call graph in JSON format for edges
            print("  Extracting call graph (agCj)...")
            try:
                cg_json = r2.get_callgraph()
                if cg_json:
                    # agCj returns a list of nodes with edges
                    for node in cg_json:
                        src_name = node.get("name", "")
                        src_offset = node.get("offset", 0)
                        for edge in node.get("imports", []):
                            result["callgraph_edges"].append({
                                "from_name": src_name,
                                "from_addr": src_offset,
                                "to_name": edge,
                            })
                    print(f"  Extracted {len(result['callgraph_edges'])} call graph edges")
            except Exception as e:
                print(f"  WARNING: Call graph JSON extraction failed: {e}")

            # Extract DOT format call graph
            print("  Extracting call graph (agCd -> DOT)...")
            try:
                dot_output = r2.r2.cmd("agCd")
                if dot_output and len(dot_output) > 50:
                    result["callgraph_dot"] = dot_output
                    print(f"  DOT output: {len(dot_output):,} chars")
                else:
                    print("  WARNING: DOT output is empty or minimal")
            except Exception as e:
                print(f"  WARNING: DOT call graph extraction failed: {e}")

    except Exception as e:
        print(f"  ERROR: Radare2 analysis failed: {e}")

    return result


# ---------------------------------------------------------------------------
# Angr CFG Recovery
# ---------------------------------------------------------------------------

def _patch_angr_arch_registers():
    """
    Patch archinfo to add missing Cortex-M4 system registers (primask, etc.)
    that angr's ARM lifter expects but archinfo does not define for ARMEL.
    """
    try:
        import archinfo
        arch = archinfo.ArchARMEL()
        # Check if primask is already defined
        try:
            arch.get_register_offset("primask")
            return  # Already present, no patch needed
        except ValueError:
            pass

        # Add missing Cortex-M special registers at unused offsets
        # These are above the normal ARM register file
        base_offset = 300  # Safe offset above normal registers
        missing_regs = {
            "primask": (base_offset, 4),
            "basepri": (base_offset + 4, 4),
            "faultmask": (base_offset + 8, 4),
            "control": (base_offset + 12, 4),
        }
        for name, (offset, size) in missing_regs.items():
            if name not in arch.registers:
                arch.registers[name] = (offset, size)

        # Monkey-patch the ArchARMEL class so new instances get these registers
        original_init = archinfo.ArchARMEL.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            for name, (offset, size) in missing_regs.items():
                if name not in self.registers:
                    self.registers[name] = (offset, size)

        archinfo.ArchARMEL.__init__ = patched_init
        print("  Patched archinfo with missing Cortex-M registers")

    except Exception as e:
        print(f"  WARNING: Could not patch archinfo: {e}")


def run_angr_analysis(bin_path: str) -> dict:
    """
    Use angr as a secondary CFG recovery tool. Returns a list of function
    addresses and names discovered by CFGFast.
    """
    print("\n[Track A] Phase 2: angr CFG recovery")
    result = {
        "angr_functions": [],
    }

    try:
        # Patch missing Cortex-M registers before loading the project
        _patch_angr_arch_registers()

        print(f"  Loading binary as blob (base=0x{MRAM_BASE:08X}, arch=ARMEL)...")
        proj = get_angr_project(bin_path)
        print(f"  Binary loaded: {proj.arch.name}, entry=0x{proj.entry:08X}")

        # Apply register patch to the project's arch instance as well
        missing_regs = {
            "primask": (300, 4),
            "basepri": (304, 4),
            "faultmask": (308, 4),
            "control": (312, 4),
        }
        for name, (offset, size) in missing_regs.items():
            if name not in proj.arch.registers:
                proj.arch.registers[name] = (offset, size)

        print(f"  Running CFGFast (timeout: {ANGR_CFG_TIMEOUT}s)...")
        start_time = time.time()

        # angr's CFGFast can be slow on large binaries; we set
        # force_complete_scan=False to speed things up
        cfg = proj.analyses.CFGFast(
            normalize=True,
            force_complete_scan=False,
            show_progressbar=False,
        )

        elapsed = time.time() - start_time
        print(f"  CFGFast completed in {elapsed:.1f}s")

        # Extract function list
        func_manager = cfg.kb.functions
        for addr, func in func_manager.items():
            func_info = {
                "address": addr,
                "name": func.name,
                "size": func.size,
                "is_simprocedure": func.is_simprocedure,
                "returning": func.returning,
                "num_blocks": len(list(func.blocks)),
            }
            result["angr_functions"].append(func_info)

        print(f"  Found {len(result['angr_functions'])} functions via angr")

    except Exception as e:
        print(f"  ERROR: angr analysis failed: {e}")
        print("  (This is non-fatal; r2 functions will be used as primary source)")

    return result


# ---------------------------------------------------------------------------
# Vector Table Parsing
# ---------------------------------------------------------------------------

def analyze_vector_table(data: bytes) -> dict:
    """Parse the ARM Cortex-M4 vector table and classify ISR handlers."""
    print("\n[Track A] Phase 3: Vector table parsing")

    vectors = parse_vector_table(data)

    # Separate into meaningful groups
    vt_info = {
        "raw_vectors": {},
        "sp_init": vectors.get("SP_Init", 0),
        "reset_handler": vectors.get("Reset_Handler", 0),
        "fault_handlers": {},
        "system_handlers": {},
        "irq_handlers": {},
        "active_irqs": [],
    }

    for name, addr in vectors.items():
        vt_info["raw_vectors"][name] = f"0x{addr:08X}"

        if "Fault" in name and addr != 0:
            vt_info["fault_handlers"][name] = f"0x{addr:08X}"
        elif name.startswith("IRQ") and addr != 0:
            vt_info["irq_handlers"][name] = f"0x{addr:08X}"
            vt_info["active_irqs"].append(name)
        elif not name.startswith("Reserved") and addr != 0:
            vt_info["system_handlers"][name] = f"0x{addr:08X}"

    print(f"  SP Init:        0x{vt_info['sp_init']:08X}")
    print(f"  Reset Handler:  0x{vt_info['reset_handler']:08X}")
    print(f"  Fault handlers: {len(vt_info['fault_handlers'])}")
    print(f"  Active IRQs:    {len(vt_info['active_irqs'])}")

    return vt_info


# ---------------------------------------------------------------------------
# Function Classification
# ---------------------------------------------------------------------------

def _resolve_ldr_string_refs(data: bytes, code_end: int, string_offsets: set) -> dict:
    """
    Resolve LDR Rd, [PC, #imm] instructions to find code locations that
    load addresses pointing to known string offsets. Returns a mapping of
    code_offset -> string_file_offset.
    """
    refs = {}  # code_offset -> string_file_offset

    for off in range(0, code_end - 1, 2):
        hw = struct.unpack_from("<H", data, off)[0]

        # 16-bit LDR Rd, [PC, #imm8*4]: encoding 0x48xx
        if (hw & 0xF800) == 0x4800:
            imm8 = hw & 0xFF
            pc = (off + 4) & ~3  # PC is +4 and word-aligned for Thumb
            pool_addr = pc + imm8 * 4
            if pool_addr + 3 < len(data):
                val = struct.unpack_from("<I", data, pool_addr)[0]
                if MRAM_BASE <= val < MRAM_BASE + len(data):
                    file_off = val - MRAM_BASE
                    if file_off in string_offsets:
                        refs[off] = file_off

    return refs


def classify_functions(
    data: bytes,
    r2_functions: list,
    angr_functions: list,
    vector_table: dict,
) -> dict:
    """
    Classify functions into categories:
    - driver: references peripheral MMIO addresses (0x4000xxxx)
    - debug: references string addresses (any string)
    - isr: appears in the vector table
    - ble: references BLE-related strings
    """
    print("\n[Track A] Phase 4: Function classification")

    classification = {
        "driver": [],
        "debug": [],
        "isr": [],
        "ble": [],
    }

    # Build a set of all known ISR addresses from vector table
    # Store both MRAM addresses AND file offsets for cross-space matching
    isr_mram_addresses = set()
    isr_file_offsets = set()
    isr_name_map = {}  # mram_addr -> vector_name
    for vt_name, addr_str in vector_table.get("raw_vectors", {}).items():
        addr = int(addr_str, 16)
        if addr != 0:
            clean = addr & ~1  # Clear thumb bit
            isr_mram_addresses.add(clean)
            isr_name_map[clean] = vt_name
            # Also store as file offset for r2 matching
            if clean >= MRAM_BASE:
                isr_file_offsets.add(clean - MRAM_BASE)

    # Collect all function addresses from both tools, deduplicating.
    # Normalize to a common address space (file offsets) for classification,
    # but keep original addresses for output.
    # r2 uses file offsets (0-based), angr uses MRAM addresses (0x18000-based)
    all_functions = {}  # file_offset -> (name, original_addr)
    r2_func_sizes = {}
    for fn in r2_functions:
        addr = fn.get("offset", 0)
        all_functions[addr] = (fn.get("name", f"fcn.{addr:08x}"), addr)
        if fn.get("size", 0) > 0:
            r2_func_sizes[addr] = fn["size"]
    for fn in angr_functions:
        mram_addr = fn.get("address", 0)
        file_off = mram_addr - MRAM_BASE if mram_addr >= MRAM_BASE else mram_addr
        if file_off not in all_functions:
            all_functions[file_off] = (fn.get("name", f"sub_{mram_addr:08x}"), mram_addr)

    # Pre-extract strings and categorize them
    print("  Extracting strings for classification...")
    all_strings = extract_strings(data, min_length=4)
    string_offsets = set()
    ble_string_offsets = set()
    for s in all_strings:
        string_offsets.add(s["offset"])
        cat = categorize_string(s["text"])
        if cat == "BLE":
            ble_string_offsets.add(s["offset"])

    # Build reference maps using two methods:
    # 1. Scan literal pools (4-byte aligned values in code region)
    # 2. Resolve LDR [PC, #imm] instructions to their pool values
    print("  Building literal pool + LDR reference maps...")
    code_end = min(CODE_REGION_END, len(data))

    # Map: code_offset -> set of properties ("periph", "string", "ble_string")
    code_offset_props = defaultdict(set)

    # Method 1: Scan for 4-byte aligned values
    for off in range(0, code_end - 3, 4):
        val = struct.unpack_from("<I", data, off)[0]
        # Peripheral address (0x4000xxxx or 0x5000xxxx for BLE controller)
        if (val & 0xF0000000) == 0x40000000 or (val & 0xF0000000) == 0x50000000:
            code_offset_props[off].add("periph")
        # String address -- check the ENTIRE firmware range, not just string region
        if MRAM_BASE <= val < MRAM_BASE + len(data):
            str_file_offset = val - MRAM_BASE
            if str_file_offset in string_offsets:
                code_offset_props[off].add("string")
                if str_file_offset in ble_string_offsets:
                    code_offset_props[off].add("ble_string")

    # Method 2: Resolve LDR [PC, #imm] instructions
    ldr_refs = _resolve_ldr_string_refs(data, code_end, string_offsets)
    for ldr_off, str_off in ldr_refs.items():
        code_offset_props[ldr_off].add("string")
        if str_off in ble_string_offsets:
            code_offset_props[ldr_off].add("ble_string")

    periph_count = sum(1 for props in code_offset_props.values() if "periph" in props)
    string_count = sum(1 for props in code_offset_props.values() if "string" in props)
    ble_count = sum(1 for props in code_offset_props.values() if "ble_string" in props)
    print(f"  Reference map: {periph_count} peripheral, {string_count} string, "
          f"{ble_count} BLE string locations")

    # Pre-populate ISR classification from the vector table directly.
    # R2/angr often miss ISR handlers because they are only reachable via
    # the hardware vector table, not via BL call instructions.
    isr_classified = set()
    for mram_addr, vt_name in isr_name_map.items():
        if vt_name.startswith("Reserved") or vt_name == "SP_Init":
            continue
        file_off = mram_addr - MRAM_BASE if mram_addr >= MRAM_BASE else mram_addr
        # Try to find a matching function name from r2 or angr
        func_name = all_functions.get(file_off, (None, None))[0]
        if func_name is None:
            func_name = vt_name
        classification["isr"].append({
            "address": f"0x{mram_addr:08X}",
            "name": func_name,
            "vector_name": vt_name,
        })
        isr_classified.add(file_off)

    print(f"  Pre-classified {len(classification['isr'])} ISR handlers from vector table")

    # Now classify each function by checking which references fall within its range
    func_count = len(all_functions)
    progress_interval = max(1, func_count // 20)

    print(f"  Classifying {func_count} functions...")

    for i, (file_offset, (name, orig_addr)) in enumerate(sorted(all_functions.items())):
        if i % progress_interval == 0 and i > 0:
            print(f"    Progress: {i}/{func_count} ({100*i//func_count}%)")

        if file_offset < 0 or file_offset >= len(data) or file_offset >= code_end:
            continue

        # Get function size from r2 data, or default to 512 bytes
        fn_size = r2_func_sizes.get(file_offset, 512)
        fn_size = min(fn_size, 4096)
        fn_start = file_offset
        fn_end = min(fn_start + fn_size, code_end)

        has_periph_ref = False
        has_string_ref = False
        has_ble_ref = False

        # Check code_offset_props for every offset within this function's range
        # Use 2-byte stride since Thumb instructions are 2 or 4 bytes
        for check_off in range(fn_start, fn_end, 2):
            props = code_offset_props.get(check_off)
            if props:
                if "periph" in props:
                    has_periph_ref = True
                if "string" in props:
                    has_string_ref = True
                if "ble_string" in props:
                    has_ble_ref = True
            if has_periph_ref and has_string_ref and has_ble_ref:
                break  # No need to check further

        if has_periph_ref:
            classification["driver"].append({
                "address": f"0x{orig_addr:08X}",
                "name": name,
            })

        if has_string_ref:
            classification["debug"].append({
                "address": f"0x{orig_addr:08X}",
                "name": name,
            })

        if has_ble_ref:
            classification["ble"].append({
                "address": f"0x{orig_addr:08X}",
                "name": name,
            })

    for cat, items in classification.items():
        print(f"  {cat}: {len(items)} functions")

    return classification


# ---------------------------------------------------------------------------
# Switch Table Detection (TBB/TBH)
# ---------------------------------------------------------------------------

def find_switch_tables(data: bytes) -> list:
    """
    Find TBB (Table Branch Byte) and TBH (Table Branch Halfword)
    instructions used for switch/case dispatch in ARM Thumb-2.

    TBB: 0xE8D0F000 + reg  ->  E8Dx F00y  where x=base_reg, y=index_reg
    TBH: 0xE8D0F010 + reg  ->  E8Dx F01y

    In practice, TBB encoding is:
      hw1 = 0xE8D0 | Rn
      hw2 = 0xF000 | Rm
    TBH encoding is:
      hw1 = 0xE8D0 | Rn
      hw2 = 0xF010 | Rm
    """
    print("\n[Track A] Phase 5: Switch table detection (TBB/TBH)")

    switch_tables = []
    code_end = min(CODE_REGION_END, len(data))

    for off in range(0, code_end - 3, 2):
        hw1 = struct.unpack_from("<H", data, off)[0]
        hw2 = struct.unpack_from("<H", data, off + 2)[0]

        # Check for TBB pattern: hw1 = 0xE8Dx, hw2 = 0xF00y
        is_tbb = (hw1 & 0xFFF0) == 0xE8D0 and (hw2 & 0xFFF0) == 0xF000
        # Check for TBH pattern: hw1 = 0xE8Dx, hw2 = 0xF01y
        is_tbh = (hw1 & 0xFFF0) == 0xE8D0 and (hw2 & 0xFFF0) == 0xF010

        if is_tbb or is_tbh:
            base_reg = hw1 & 0xF
            index_reg = hw2 & 0xF
            table_type = "TBB" if is_tbb else "TBH"

            # The jump table follows immediately after the instruction
            table_start = off + 4
            table_addr = MRAM_BASE + off

            # Try to determine table size by looking at entries
            # TBB entries are 1 byte each, TBH entries are 2 bytes each
            entry_size = 1 if is_tbb else 2
            entries = []
            max_entries = 256 if is_tbb else 128

            for j in range(max_entries):
                entry_off = table_start + j * entry_size
                if entry_off + entry_size > len(data):
                    break

                if is_tbb:
                    entry_val = data[entry_off]
                else:
                    entry_val = struct.unpack_from("<H", data, entry_off)[0]

                # Entry value of 0 is typically a valid case (fall-through)
                # but a sequence of non-zero values that are too large
                # probably means we've gone past the table
                if entry_val > 0x200:
                    break

                # Calculate target address
                target = table_addr + 4 + entry_val * 2
                entries.append({
                    "index": j,
                    "offset_value": entry_val,
                    "target_addr": f"0x{target:08X}",
                })

                # Heuristic: if the next byte pair looks like a Thumb instruction
                # prolog (PUSH), the table probably ended
                peek_off = table_start + (j + 1) * entry_size
                if peek_off + 1 < len(data):
                    peek = struct.unpack_from("<H", data, peek_off)[0]
                    if (peek & 0xFF00) == 0xB500:
                        break

            if len(entries) >= 2:
                switch_tables.append({
                    "address": f"0x{table_addr:08X}",
                    "file_offset": off,
                    "type": table_type,
                    "base_reg": f"r{base_reg}",
                    "index_reg": f"r{index_reg}",
                    "num_entries": len(entries),
                    "entries": entries[:32],  # Limit output size
                })

    print(f"  Found {len(switch_tables)} switch tables "
          f"(TBB: {sum(1 for t in switch_tables if t['type']=='TBB')}, "
          f"TBH: {sum(1 for t in switch_tables if t['type']=='TBH')})")

    return switch_tables


# ---------------------------------------------------------------------------
# Function Prolog Statistics
# ---------------------------------------------------------------------------

def analyze_prologs(data: bytes) -> dict:
    """
    Find all function prologues and compute statistics about the patterns used.
    """
    print("\n[Track A] Phase 6: Function prolog statistics")

    code_end = min(CODE_REGION_END, len(data))
    prologs = find_function_prologs(data, start=0, end=code_end)

    # Analyze the PUSH patterns
    push_patterns = defaultdict(int)
    for off in prologs:
        hw = struct.unpack_from("<H", data, off)[0]
        if (hw & 0xFF00) == 0xB500:
            # 16-bit PUSH {regs, LR}
            reg_list = hw & 0xFF
            regs = []
            for bit in range(8):
                if reg_list & (1 << bit):
                    regs.append(f"r{bit}")
            regs.append("lr")
            pattern = "PUSH {" + ", ".join(regs) + "}"
            push_patterns[pattern] += 1
        elif hw == 0xE92D:
            # 32-bit PUSH.W
            if off + 2 < len(data):
                hw2 = struct.unpack_from("<H", data, off + 2)[0]
                reg_list = hw2
                regs = []
                for bit in range(13):
                    if reg_list & (1 << bit):
                        regs.append(f"r{bit}")
                if reg_list & 0x4000:
                    regs.append("lr")
                pattern = "PUSH.W {" + ", ".join(regs) + "}"
                push_patterns[pattern] += 1

    # Sort by frequency
    sorted_patterns = sorted(push_patterns.items(), key=lambda x: -x[1])

    print(f"  Total function prologues found: {len(prologs)}")
    print(f"  Unique PUSH patterns: {len(push_patterns)}")
    print("  Top 10 patterns:")
    for pattern, count in sorted_patterns[:10]:
        print(f"    {count:5d}x  {pattern}")

    return {
        "total_prologs": len(prologs),
        "unique_patterns": len(push_patterns),
        "top_patterns": [{"pattern": p, "count": c} for p, c in sorted_patterns[:50]],
        "prolog_offsets_sample": [f"0x{off:06X}" for off in prologs[:100]],
    }


# ---------------------------------------------------------------------------
# Capstone Detail Disassembly of Key Functions
# ---------------------------------------------------------------------------

def disassemble_key_functions(data: bytes, vector_table: dict) -> list:
    """
    Use capstone to disassemble key functions identified from the vector table
    (Reset_Handler, fault handlers) and return instruction-level detail.
    """
    print("\n[Track A] Phase 7: Capstone detail disassembly of key functions")

    md = get_capstone_md()
    key_functions = []

    # Disassemble Reset_Handler and other important vectors
    targets = {
        "Reset_Handler": vector_table.get("reset_handler", 0),
        "HardFault_Handler": int(
            vector_table.get("fault_handlers", {}).get("HardFault_Handler", "0x0"), 16
        ),
        "SysTick_Handler": int(
            vector_table.get("raw_vectors", {}).get("SysTick_Handler", "0x0"), 16
        ),
    }

    for name, addr in targets.items():
        if addr == 0:
            continue

        # Clear thumb bit and compute file offset
        clean_addr = addr & ~1
        if clean_addr >= MRAM_BASE:
            file_offset = clean_addr - MRAM_BASE
        else:
            file_offset = clean_addr

        if file_offset >= len(data) or file_offset < 0:
            print(f"  Skipping {name}: offset 0x{file_offset:X} out of range")
            continue

        print(f"  Disassembling {name} at 0x{addr:08X} (file offset 0x{file_offset:06X})...")

        instrs = disasm_function(data, file_offset, max_insns=100, md=md)
        disasm_lines = []
        for ins in instrs:
            line = f"0x{ins.address:08X}: {ins.mnemonic:8s} {ins.op_str}"
            disasm_lines.append(line)

        key_functions.append({
            "name": name,
            "address": f"0x{addr:08X}",
            "file_offset": f"0x{file_offset:06X}",
            "num_instructions": len(instrs),
            "disassembly": disasm_lines[:50],  # Limit output
        })

        # Print first few instructions
        for line in disasm_lines[:5]:
            print(f"    {line}")
        if len(disasm_lines) > 5:
            print(f"    ... ({len(disasm_lines)} total instructions)")

    return key_functions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start_time = time.time()
    print("=" * 70)
    print("Track A: Comprehensive ARM Disassembly & Function Mapping")
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

    # Phase 1: Radare2 analysis
    r2_result = run_r2_analysis(DEFAULT_BIN)

    # Phase 2: angr CFG recovery
    angr_result = run_angr_analysis(DEFAULT_BIN)

    # Phase 3: Vector table
    vt_info = analyze_vector_table(data)

    # Phase 4: Function classification
    classification = classify_functions(
        data,
        r2_result["r2_functions"],
        angr_result["angr_functions"],
        vt_info,
    )

    # Phase 5: Switch tables
    switch_tables = find_switch_tables(data)

    # Phase 6: Prolog statistics
    prolog_stats = analyze_prologs(data)

    # Phase 7: Capstone detail disassembly
    key_disasm = disassemble_key_functions(data, vt_info)

    # ---------------------------------------------------------------------------
    # Assemble final output
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Assembling output...")

    # Deduplicate function lists for final count
    all_func_addrs = set()
    for fn in r2_result["r2_functions"]:
        all_func_addrs.add(fn.get("offset", 0))
    for fn in angr_result["angr_functions"]:
        all_func_addrs.add(fn.get("address", 0))

    output = {
        "metadata": {
            "binary": DEFAULT_BIN,
            "binary_size": bin_size,
            "base_address": f"0x{MRAM_BASE:08X}",
            "analysis_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(time.time() - start_time, 1),
        },
        "total_functions": len(all_func_addrs),
        "r2_functions": r2_result["r2_functions"],
        "angr_functions": angr_result["angr_functions"],
        "vector_table": vt_info,
        "function_classification": classification,
        "switch_tables": switch_tables,
        "prolog_count": prolog_stats["total_prologs"],
        "prolog_statistics": prolog_stats,
        "callgraph_edges": r2_result["callgraph_edges"],
        "key_function_disassembly": key_disasm,
    }

    # Save JSON output
    json_path = save_output("track_a_functions.json", output)

    # Save DOT call graph
    dot_path = OUTPUT_DIR / "track_a_callgraph.dot"
    dot_content = r2_result.get("callgraph_dot", "")
    if not dot_content or len(dot_content) < 50:
        # Generate a basic DOT from our callgraph_edges
        print("  Generating DOT call graph from edge data...")
        lines = [
            'digraph callgraph {',
            '  rankdir=LR;',
            '  node [shape=box fontname="Courier" fontsize=10];',
            '  edge [arrowhead=normal];',
        ]
        seen_edges = set()
        for edge in r2_result["callgraph_edges"][:5000]:  # Limit for readability
            from_name = edge.get("from_name", "?").replace('"', '\\"')
            to_name = edge.get("to_name", "?").replace('"', '\\"')
            key = (from_name, to_name)
            if key not in seen_edges:
                seen_edges.add(key)
                lines.append(f'  "{from_name}" -> "{to_name}";')
        lines.append("}")
        dot_content = "\n".join(lines)

    with open(dot_path, "w") as f:
        f.write(dot_content)
    print(f"  Saved: {dot_path} ({dot_path.stat().st_size:,} bytes)")

    # Final summary
    elapsed = time.time() - start_time
    print()
    print("=" * 70)
    print("Track A Summary")
    print("=" * 70)
    print(f"  Total unique functions:  {output['total_functions']}")
    print(f"  r2 functions:            {len(output['r2_functions'])}")
    print(f"  angr functions:          {len(output['angr_functions'])}")
    print(f"  Vector table ISRs:       {len(vt_info['active_irqs'])}")
    print(f"  Driver functions:        {len(classification['driver'])}")
    print(f"  Debug functions:         {len(classification['debug'])}")
    print(f"  ISR functions:           {len(classification['isr'])}")
    print(f"  BLE functions:           {len(classification['ble'])}")
    print(f"  Switch tables:           {len(switch_tables)}")
    print(f"  Function prologues:      {prolog_stats['total_prologs']}")
    print(f"  Call graph edges:        {len(r2_result['callgraph_edges'])}")
    print(f"  Elapsed time:            {elapsed:.1f}s")
    print()
    print(f"  Output: {json_path}")
    print(f"  Output: {dot_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
