#!/usr/bin/env python3
"""
Track E: QP RTOS Architecture Analysis
=======================================

Analyzes the QP (Quantum Platform) real-time operating system architecture
in the Whoop 5.0 firmware. Identifies Active Objects, enumerates signals,
reconstructs state machines, and maps inter-AO communication.

The firmware uses QP/C RTOS with 18+ Active Objects communicating via
publish-subscribe signals and direct event posting.

Usage:
    python3 track_e_rtos.py

Output:
    analysis/output/track_e_rtos.json
"""

import json
import os
import re
import struct
import sys
import time
from collections import defaultdict, Counter
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

# String-dense regions
STRING_REGION_START = 0x0A0000
STRING_REGION_END = 0x170000

# QP framework function patterns to search for
QP_FUNCTION_PATTERNS = [
    "QF_onStartup",
    "QActive_start",
    "QActive_post",
    "QActive_postLIFO",
    "QF_publish",
    "QF_tick",
    "QF_TICK_RATE",
    "QHsm_init",
    "QHsm_dispatch",
    "QMsm_init",
    "QMsm_dispatch",
    "Q_ASSERT",
    "QEvt_ctor",
    "QF_newX_",
    "QF_gc",
    "QTimeEvt_armX",
    "QTimeEvt_disarm",
    "QF_poolInit",
    "QF_onCleanup",
]

# Known AO source file suffixes for identification
KNOWN_AO_NAMES = {
    "supervisor_ao.c": "Supervisor",
    "ble.c": "BLE",
    "ble_cmd_ao.c": "BLE_Command",
    "sensors_ao.c": "Sensors",
    "flash.c": "Flash",
    "analytics_ao.c": "Analytics",
    "i2c_ao.c": "I2C",
    "listener_ao.c": "Listener",
    "onsemi_fuel_gauge_ao.c": "Fuel_Gauge",
    "lc709205f_ao.c": "LC709205F",
    "ui_manager_ao.c": "UI_Manager",
    "led_ui_ao.c": "LED_UI",
    "lp5562_ao.c": "LP5562",
    "haptics_ao.c": "Haptics",
    "drv2625_ao.c": "DRV2625",
    "temp_sensors_ao.c": "Temp_Sensors",
    "as6221_ao.c": "AS6221",
    "tag_reader_ao.c": "Tag_Reader",
    "ecg_control_ao.c": "ECG_Control",
    "debugmenu_ao.c": "Debug_Menu",
    "itest_ao.c": "ITEST",
    "itest_listener_ao.c": "ITEST_Listener",
    "itest_temp_sensors_ao.c": "ITEST_Temp_Sensors",
    "itest_fuel_gauge_ao.c": "ITEST_Fuel_Gauge",
    "itest_ui_manager_ao.c": "ITEST_UI_Manager",
    "whoop_cordio_ao.c": "Whoop_Cordio",
}


# ---------------------------------------------------------------------------
# Phase 1: Active Object Identification
# ---------------------------------------------------------------------------

def find_active_objects(data: bytes, all_strings: list) -> list:
    """
    Find all Active Objects by searching for source file path strings
    ending in _ao.c. Each _ao.c string corresponds to one Active Object.
    """
    print("[Track E] Phase 1: Active Object identification")
    start_time = time.time()

    active_objects = []

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]

        # Match strings containing _ao.c (source file paths)
        if "_ao.c" in text:
            # Extract the filename from the path
            path = text.strip()
            filename = path.rsplit("/", 1)[-1] if "/" in path else path

            # Look up the AO name
            ao_name = KNOWN_AO_NAMES.get(filename, filename.replace("_ao.c", "").replace(".c", ""))

            # Determine if this is a test/integration AO
            is_test = "test/" in path or "itest" in path.lower()

            # Find code references to this string
            code_refs = find_string_references(data, offset, search_range=(0, CODE_REGION_END))

            # Try to identify the containing function for each reference
            func_addrs = []
            for ref in code_refs[:10]:  # limit to avoid excessive search
                func_start = find_function_start(data, ref)
                if func_start is not None:
                    func_addrs.append(func_start + MRAM_BASE)

            ao_entry = {
                "name": ao_name,
                "source_file": path,
                "filename": filename,
                "string_offset": offset,
                "string_address": offset + MRAM_BASE,
                "is_test_ao": is_test,
                "code_refs": [r + MRAM_BASE for r in code_refs],
                "code_ref_count": len(code_refs),
                "containing_functions": sorted(set(func_addrs)),
            }
            active_objects.append(ao_entry)

    # Sort by string offset (roughly corresponds to code layout order)
    active_objects.sort(key=lambda x: x["string_offset"])

    # Separate production and test AOs
    prod_aos = [ao for ao in active_objects if not ao["is_test_ao"]]
    test_aos = [ao for ao in active_objects if ao["is_test_ao"]]

    elapsed = time.time() - start_time
    print(f"  Found {len(prod_aos)} production Active Objects, {len(test_aos)} test AOs in {elapsed:.1f}s")
    for ao in prod_aos:
        print(f"    {ao['name']:25s} ({ao['source_file']}) - {ao['code_ref_count']} code refs")

    return active_objects


# ---------------------------------------------------------------------------
# Phase 2: Signal Enumeration
# ---------------------------------------------------------------------------

def enumerate_signals(data: bytes, all_strings: list) -> dict:
    """
    Find all QP signal name strings (ending with _SIG) and categorize
    them by subsystem prefix.
    """
    print("\n[Track E] Phase 2: Signal enumeration")
    start_time = time.time()

    signals_by_subsystem = defaultdict(list)
    all_signals = []

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]

        # Match signal name strings (must end with _SIG or contain _SIG)
        if "_SIG" not in text:
            continue

        # Skip log format strings (contain %, :, etc. before _SIG)
        if text.startswith("%") or ": " in text:
            continue

        # Extract the subsystem prefix (everything before the first _)
        parts = text.split("_")
        if len(parts) < 2:
            subsystem = "UNKNOWN"
        else:
            subsystem = parts[0]

        # Find code references to this signal string
        code_refs = find_string_references(data, offset, search_range=(0, CODE_REGION_END))

        signal_entry = {
            "name": text,
            "offset": offset,
            "address": offset + MRAM_BASE,
            "subsystem": subsystem,
            "code_refs": [r + MRAM_BASE for r in code_refs[:5]],
            "code_ref_count": len(code_refs),
        }

        signals_by_subsystem[subsystem].append(signal_entry)
        all_signals.append(signal_entry)

    # Build signal counts
    signal_counts = {}
    for subsystem, sigs in sorted(signals_by_subsystem.items(), key=lambda x: -len(x[1])):
        signal_counts[subsystem] = len(sigs)

    elapsed = time.time() - start_time
    print(f"  Found {len(all_signals)} unique signals across {len(signals_by_subsystem)} subsystems in {elapsed:.1f}s")
    print("  Top subsystems by signal count:")
    for subsystem in sorted(signal_counts, key=lambda k: -signal_counts[k])[:15]:
        count = signal_counts[subsystem]
        print(f"    {subsystem:25s}: {count:3d} signals")

    # Also extract the QP built-in signals
    qp_builtin = [s for s in all_signals if s["subsystem"] == "Q"]
    if qp_builtin:
        print(f"  QP built-in signals: {', '.join(s['name'] for s in qp_builtin)}")

    return {
        "signals_by_subsystem": {k: v for k, v in signals_by_subsystem.items()},
        "signal_counts": signal_counts,
        "all_signals": all_signals,
        "total_signals": len(all_signals),
        "qp_builtin_signals": [s["name"] for s in qp_builtin],
    }


# ---------------------------------------------------------------------------
# Phase 3: QP Framework Analysis
# ---------------------------------------------------------------------------

def analyze_qp_framework(data: bytes, all_strings: list) -> dict:
    """
    Search for QP framework-related strings and code patterns to
    understand the RTOS configuration.
    """
    print("\n[Track E] Phase 3: QP framework analysis")
    start_time = time.time()

    # Find QP-related strings
    qp_strings = []
    qp_patterns = [
        "QF_", "QActive", "QHsm", "QMsm", "Q_tran", "Q_HANDLED",
        "Q_RET", "QEP_", "QEvt", "Q_ASSERT", "QP/", "qpc",
        "Q_USER_SIG", "Q_EMPTY_SIG", "Q_ENTRY_SIG", "Q_EXIT_SIG",
        "Q_INIT_SIG", "QF_onStartup", "QF_tick",
    ]

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]
        for pattern in qp_patterns:
            if pattern in text:
                code_refs = find_string_references(data, offset, search_range=(0, CODE_REGION_END))
                qp_strings.append({
                    "text": text,
                    "offset": offset,
                    "address": offset + MRAM_BASE,
                    "pattern_matched": pattern,
                    "code_refs": [r + MRAM_BASE for r in code_refs[:5]],
                    "code_ref_count": len(code_refs),
                })
                break

    # Search for QActive_start call patterns in code
    # QActive_start signature: takes AO pointer, priority, event queue, queue size,
    #   stack, stack size, initial event
    # In ARM: typically BL to a fixed address with r0=AO pointer
    qactive_start_refs = []
    for s in qp_strings:
        if "QActive" in s["text"] or "QF_onStartup" in s["text"]:
            qactive_start_refs.extend(s["code_refs"])

    # Search for QP tick rate configuration
    # QF_TICK_RATE is typically defined as a constant
    tick_rate_info = {}
    for s in all_strings:
        text = s["text"].lower()
        if "tick" in text and ("rate" in text or "hz" in text or "freq" in text):
            tick_rate_info[s["text"]] = {
                "offset": s["offset"],
                "address": s["offset"] + MRAM_BASE,
            }

    # Search for Q_ASSERT pattern - this tells us about the QP error handling
    q_assert_strings = [s for s in qp_strings if "Q_ASSERT" in s["text"]]

    elapsed = time.time() - start_time
    print(f"  Found {len(qp_strings)} QP framework strings in {elapsed:.1f}s")
    for s in qp_strings:
        print(f"    0x{s['offset']:06X}: {s['text'][:80]}")

    return {
        "strings": qp_strings,
        "qactive_start_references": qactive_start_refs,
        "tick_rate_info": tick_rate_info,
        "q_assert_strings": [s["text"] for s in q_assert_strings],
        "total_qp_strings": len(qp_strings),
    }


# ---------------------------------------------------------------------------
# Phase 4: SysTick / Tick Rate Analysis
# ---------------------------------------------------------------------------

def analyze_systick(data: bytes) -> dict:
    """
    Analyze the SysTick handler and attempt to determine the tick rate.
    The SysTick handler is at vector table entry #15.
    """
    print("\n[Track E] Phase 4: SysTick handler analysis")
    start_time = time.time()

    # Parse vector table
    vectors = parse_vector_table(data)
    systick_addr_raw = vectors.get("SysTick_Handler", 0)
    systick_addr = systick_addr_raw & ~1  # Clear Thumb bit

    result = {
        "handler_addr": f"0x{systick_addr_raw:08X}",
        "handler_addr_thumb_cleared": f"0x{systick_addr:08X}",
        "disassembly": [],
        "tick_rate_estimate": None,
        "notes": [],
    }

    if systick_addr == 0:
        result["notes"].append("SysTick handler is null (not configured)")
        return result

    # Calculate file offset
    # Binary starts at MRAM_BASE (0x18000), vector table at file offset VECTOR_TABLE_OFFSET (0x200)
    # Address = MRAM_BASE + file_offset OR file_offset = address - MRAM_BASE
    file_offset = systick_addr - MRAM_BASE
    if file_offset < 0 or file_offset >= len(data):
        result["notes"].append(
            f"SysTick handler address 0x{systick_addr:08X} maps to file offset "
            f"0x{file_offset:06X} which is outside binary bounds"
        )
        # The SysTick address might point into a data/string region
        # Check if the bytes look like ASCII text
        if 0 <= file_offset < len(data):
            raw = data[file_offset:file_offset + 32]
            is_ascii = all(0x20 <= b <= 0x7E or b in (0, 0x0A, 0x0D) for b in raw)
            if is_ascii:
                result["notes"].append(
                    f"Bytes at SysTick offset appear to be ASCII text: "
                    f"'{raw.decode('ascii', errors='replace')[:64]}'"
                )
                result["notes"].append(
                    "This suggests the vector table address mapping differs from "
                    "the simple MRAM_BASE offset calculation. The SysTick handler "
                    "may be relocated or the binary has a non-standard layout."
                )
        return result

    # Check if the offset points to code or data
    raw_bytes = data[file_offset:file_offset + 32]
    is_likely_ascii = sum(1 for b in raw_bytes if 0x20 <= b <= 0x7E) > 24
    if is_likely_ascii:
        result["notes"].append(
            f"SysTick offset 0x{file_offset:06X} appears to contain ASCII data, "
            f"not code. The address-to-offset mapping may need adjustment."
        )
        result["notes"].append(
            f"ASCII content: '{raw_bytes.decode('ascii', errors='replace')}'"
        )

    # Try disassembly regardless
    md = get_capstone_md()
    try:
        instrs = list(md.disasm(data[file_offset:file_offset + 256], systick_addr))
        for ins in instrs[:30]:
            entry = {
                "address": f"0x{ins.address:08X}",
                "bytes": ins.bytes.hex(),
                "mnemonic": ins.mnemonic,
                "op_str": ins.op_str,
            }
            result["disassembly"].append(entry)

        # Look for timer-related patterns
        for ins in instrs[:30]:
            if ins.mnemonic in ("bl", "bl.w"):
                result["notes"].append(
                    f"BL call at 0x{ins.address:08X} -> {ins.op_str} (possible QF_tick call)"
                )
    except Exception as e:
        result["notes"].append(f"Disassembly error: {e}")

    # Also search the binary for SysTick configuration (CSR register 0xE000E010)
    systick_csr_addr = struct.pack("<I", 0xE000E010)
    systick_rvr_addr = struct.pack("<I", 0xE000E014)  # Reload Value Register

    csr_refs = []
    off = 0
    while off < CODE_REGION_END:
        idx = data.find(systick_csr_addr, off, CODE_REGION_END)
        if idx < 0:
            break
        csr_refs.append(idx)
        off = idx + 4

    rvr_refs = []
    off = 0
    while off < CODE_REGION_END:
        idx = data.find(systick_rvr_addr, off, CODE_REGION_END)
        if idx < 0:
            break
        rvr_refs.append(idx)
        off = idx + 4

    result["systick_csr_refs"] = [f"0x{r:06X}" for r in csr_refs]
    result["systick_rvr_refs"] = [f"0x{r:06X}" for r in rvr_refs]

    if rvr_refs:
        # Try to find the reload value near the RVR reference
        for ref in rvr_refs:
            # Look nearby for a constant that could be the reload value
            for delta in range(-32, 32, 4):
                check_off = ref + delta
                if 0 <= check_off < len(data) - 4:
                    val = struct.unpack_from("<I", data, check_off)[0]
                    # Common tick rates: 1ms=48000 (48MHz), 10ms=480000, etc.
                    if 1000 <= val <= 1000000:
                        freq_hz = 48000000 / val  # Apollo4 runs at 48MHz typically
                        if 10 <= freq_hz <= 10000:
                            result["notes"].append(
                                f"Potential SysTick reload value {val} at offset "
                                f"0x{check_off:06X} -> ~{freq_hz:.0f} Hz tick rate "
                                f"(assuming 48 MHz clock)"
                            )

    # Common QP tick rate is 100 Hz (10ms) or 1000 Hz (1ms)
    result["tick_rate_estimate"] = "Likely 100-1000 Hz (typical QP configuration)"

    elapsed = time.time() - start_time
    print(f"  SysTick analysis complete in {elapsed:.1f}s")
    print(f"  Handler at {result['handler_addr']}")
    print(f"  SysTick CSR references: {len(csr_refs)}")
    print(f"  SysTick RVR references: {len(rvr_refs)}")
    for note in result["notes"]:
        print(f"    Note: {note[:100]}")

    return result


# ---------------------------------------------------------------------------
# Phase 5: State Machine Reconstruction
# ---------------------------------------------------------------------------

def reconstruct_state_machines(data: bytes, active_objects: list, all_strings: list) -> list:
    """
    Attempt to reconstruct QP state machine structures by analyzing
    code near AO source file references and looking for state handler
    patterns.
    """
    print("\n[Track E] Phase 5: State machine reconstruction")
    start_time = time.time()

    md = get_capstone_md()
    state_machines = []

    # In QP, state handlers have the signature: QState handler(void *me, QEvt const *e)
    # They return QState values via macros like Q_HANDLED(), Q_TRAN(&target), Q_SUPER(&parent)
    # State names often appear as strings in debug output

    # Search for state-related strings
    state_strings = []
    state_patterns = [
        "_initial", "_active", "_idle", "_busy", "_ready", "_running",
        "_stopped", "_error", "_init", "_disabled", "_enabled",
        "_connected", "_disconnected", "_waiting", "_processing",
        "_charging", "_discharging", "_on_body", "_off_body",
    ]

    for s in all_strings:
        text = s["text"]
        offset = s["offset"]
        text_lower = text.lower()

        # Look for state transition log messages
        if any(p in text_lower for p in state_patterns):
            if any(ao["name"].lower() in text_lower or
                   ao["filename"].replace("_ao.c", "") in text_lower
                   for ao in active_objects):
                state_strings.append({
                    "text": text,
                    "offset": offset,
                    "address": offset + MRAM_BASE,
                })

    # For each AO, try to find state handler functions
    for ao in active_objects:
        if ao["is_test_ao"]:
            continue

        sm_entry = {
            "ao_name": ao["name"],
            "source_file": ao["source_file"],
            "states": [],
            "state_handler_candidates": [],
            "transition_strings": [],
        }

        ao_name_lower = ao["name"].lower().replace("_", "")
        file_stem = ao["filename"].replace("_ao.c", "").replace(".c", "")

        # Find state-related strings for this AO
        for s in all_strings:
            text = s["text"]
            text_lower = text.lower().replace("_", "")
            if (ao_name_lower in text_lower or file_stem in text.lower()) and \
               any(p.replace("_", "") in text_lower for p in state_patterns):
                sm_entry["transition_strings"].append({
                    "text": text[:120],
                    "offset": s["offset"],
                })

        # Look for function prologs near the AO's code references
        # These are candidate state handler functions
        for ref_addr in ao.get("code_refs", [])[:5]:
            ref_offset = ref_addr - MRAM_BASE
            if ref_offset < 0 or ref_offset >= len(data):
                continue

            # Search backward for function start
            func_start = find_function_start(data, ref_offset)
            if func_start is not None:
                # Try to find nearby functions (state handlers are typically
                # clustered together in the same compilation unit)
                nearby_prologs = find_function_prologs(
                    data,
                    start=max(0, func_start - 0x2000),
                    end=min(len(data), func_start + 0x2000),
                )
                sm_entry["state_handler_candidates"] = [
                    f"0x{(p + MRAM_BASE):08X}" for p in nearby_prologs[:30]
                ]
                break

        # Extract likely state names from transition strings
        for ts in sm_entry["transition_strings"]:
            text = ts["text"]
            for pattern in state_patterns:
                if pattern in text.lower():
                    # Extract state name context
                    sm_entry["states"].append({
                        "pattern": pattern,
                        "context": text[:80],
                    })
                    break

        if sm_entry["states"] or sm_entry["transition_strings"] or sm_entry["state_handler_candidates"]:
            state_machines.append(sm_entry)

    elapsed = time.time() - start_time
    print(f"  Reconstructed {len(state_machines)} state machine structures in {elapsed:.1f}s")
    for sm in state_machines:
        n_states = len(sm["states"])
        n_trans = len(sm["transition_strings"])
        n_handlers = len(sm["state_handler_candidates"])
        print(f"    {sm['ao_name']:25s}: {n_states} states, {n_trans} transition strings, {n_handlers} handler candidates")

    return state_machines


# ---------------------------------------------------------------------------
# Phase 6: Signal Flow / Inter-AO Communication
# ---------------------------------------------------------------------------

def analyze_signal_flow(data: bytes, active_objects: list, signal_data: dict) -> dict:
    """
    Map inter-AO communication by analyzing which signals reference
    which AO source files and which subsystems they connect.
    """
    print("\n[Track E] Phase 6: Signal flow analysis")
    start_time = time.time()

    # Build a map of AO name -> subsystem prefix used in signals
    ao_to_signal_prefix = {}
    for ao in active_objects:
        name = ao["name"]
        name_upper = name.upper()
        # Map AO names to their likely signal prefixes
        prefix_map = {
            "Supervisor": "SUPERVISOR",
            "BLE": "BLE",
            "BLE_Command": "BLE",
            "Sensors": "SENSORS",
            "Flash": "FLASH",
            "Analytics": "ANALYTICS",
            "I2C": "I2C",
            "Listener": "LISTENER",
            "Fuel_Gauge": "FUEL_GAUGE",
            "LC709205F": "LC709205F",
            "UI_Manager": "UI",
            "LED_UI": "LED",
            "LP5562": "LP5562",
            "Haptics": "HAPTICS",
            "DRV2625": "DRV2625",
            "Temp_Sensors": "TEMP",
            "AS6221": "AS6221",
            "Tag_Reader": "TAG",
            "ECG_Control": "ECG",
            "Debug_Menu": "DEBUGMENU",
            "ITEST": "ITEST",
            "Whoop_Cordio": "BLE",
        }
        ao_to_signal_prefix[name] = prefix_map.get(name, name_upper)

    # Analyze signal flow patterns
    signal_flow = {
        "ao_to_signal_prefix_map": ao_to_signal_prefix,
        "inter_ao_signals": [],
        "signal_posting_patterns": [],
    }

    # Identify cross-subsystem signals (signals that reference multiple subsystems)
    signals_by_sub = signal_data.get("signals_by_subsystem", {})
    cross_signals = []
    for subsystem, sigs in signals_by_sub.items():
        for sig in sigs:
            sig_name = sig["name"]
            # Check if the signal name references another subsystem
            for other_sub in signals_by_sub:
                if other_sub != subsystem and other_sub.lower() in sig_name.lower():
                    cross_signals.append({
                        "signal": sig_name,
                        "source_subsystem": subsystem,
                        "target_subsystem": other_sub,
                    })

    signal_flow["cross_subsystem_signals"] = cross_signals

    # Build communication graph (which subsystems talk to which)
    comm_graph = defaultdict(set)

    # Signals with READY_REPORT, ERROR_REPORT, DISABLE_REPORT follow a pattern:
    # Child AO -> Supervisor/Parent
    report_sigs = []
    for subsystem, sigs in signals_by_sub.items():
        for sig in sigs:
            name = sig["name"]
            if "READY_REPORT" in name:
                report_sigs.append({"signal": name, "from": subsystem, "to": "SUPERVISOR", "type": "ready_report"})
                comm_graph[subsystem].add("SUPERVISOR")
            elif "ERROR_REPORT" in name:
                report_sigs.append({"signal": name, "from": subsystem, "to": "SUPERVISOR", "type": "error_report"})
                comm_graph[subsystem].add("SUPERVISOR")
            elif "DISABLE_REPORT" in name:
                report_sigs.append({"signal": name, "from": subsystem, "to": "SUPERVISOR", "type": "disable_report"})
                comm_graph[subsystem].add("SUPERVISOR")

    signal_flow["report_signals"] = report_sigs
    signal_flow["communication_graph"] = {k: sorted(v) for k, v in comm_graph.items()}

    # Identify Supervisor's outgoing signals (POLL, SOC, etc.)
    supervisor_outgoing = []
    for sig in signals_by_sub.get("SUPERVISOR", []):
        supervisor_outgoing.append(sig["name"])
    signal_flow["supervisor_outgoing_signals"] = supervisor_outgoing

    # I2C bus signals flow to multiple AOs
    i2c_signals = signals_by_sub.get("I2C", [])
    i2c_consumers = set()
    for sig in i2c_signals:
        name = sig["name"]
        if "STATUS" in name or "HUNG" in name or "ERROR" in name or "SUCCESS" in name:
            # These likely go to all I2C-using AOs
            i2c_consumers.update(["AS6221", "DRV2625", "LP5562", "LC709205F", "FUEL_GAUGE"])
    signal_flow["i2c_broadcast_consumers"] = sorted(i2c_consumers)

    elapsed = time.time() - start_time
    print(f"  Signal flow analysis complete in {elapsed:.1f}s")
    print(f"  Cross-subsystem signals: {len(cross_signals)}")
    print(f"  Report signals (child->supervisor): {len(report_sigs)}")
    print(f"  Communication graph edges: {sum(len(v) for v in comm_graph.values())}")

    return signal_flow


# ---------------------------------------------------------------------------
# Phase 7: Event Pool and Timer Analysis
# ---------------------------------------------------------------------------

def analyze_event_pools_and_timers(data: bytes, all_strings: list) -> dict:
    """
    Search for QP event pool initialization and timer configuration
    patterns to understand memory allocation for events.
    """
    print("\n[Track E] Phase 7: Event pool and timer analysis")
    start_time = time.time()

    result = {
        "event_pool_strings": [],
        "timer_strings": [],
        "pool_init_candidates": [],
    }

    # Search for pool/timer related strings
    for s in all_strings:
        text = s["text"]
        text_lower = text.lower()

        if any(k in text_lower for k in ["pool", "event pool", "qf_pool", "queue"]):
            result["event_pool_strings"].append({
                "text": text[:120],
                "offset": s["offset"],
            })

        if any(k in text_lower for k in ["timer", "timeout", "periodic", "one_shot", "arm_timer"]):
            result["timer_strings"].append({
                "text": text[:120],
                "offset": s["offset"],
            })

    # Search for QF_poolInit pattern in code
    # QF_poolInit takes: poolSto, poolSize, evtSize
    # Look for sequences of 3 parameters followed by a BL call
    # This is heuristic - we look for common pool sizes

    # Common event sizes: small=32, medium=64, large=256
    for evt_size in [32, 64, 128, 256]:
        evt_bytes = struct.pack("<I", evt_size)
        off = 0
        while off < CODE_REGION_END:
            idx = data.find(evt_bytes, off, CODE_REGION_END)
            if idx < 0:
                break
            # Check if this could be a literal pool entry near pool init code
            if idx % 4 == 0:  # Word-aligned
                # Check surrounding context for other pool-related values
                nearby_vals = set()
                for delta in range(-16, 20, 4):
                    check = idx + delta
                    if 0 <= check < len(data) - 4:
                        val = struct.unpack_from("<I", data, check)[0]
                        nearby_vals.add(val)

                # If we see multiple event-pool-like sizes nearby, likely a pool init area
                pool_sizes_found = nearby_vals & {16, 24, 32, 48, 64, 128, 256, 512}
                if len(pool_sizes_found) >= 2:
                    result["pool_init_candidates"].append({
                        "offset": idx,
                        "event_size": evt_size,
                        "nearby_sizes": sorted(pool_sizes_found),
                    })
            off = idx + 4

    elapsed = time.time() - start_time
    print(f"  Event pool strings: {len(result['event_pool_strings'])}")
    print(f"  Timer strings: {len(result['timer_strings'])}")
    print(f"  Pool init candidates: {len(result['pool_init_candidates'])}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Track E: QP RTOS Architecture Analysis")
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

    # Phase 1: Active Object identification
    active_objects = find_active_objects(data, all_strings)

    # Phase 2: Signal enumeration
    signal_data = enumerate_signals(data, all_strings)

    # Phase 3: QP framework analysis
    qp_framework = analyze_qp_framework(data, all_strings)

    # Phase 4: SysTick analysis
    systick = analyze_systick(data)

    # Phase 5: State machine reconstruction
    state_machines = reconstruct_state_machines(data, active_objects, all_strings)

    # Phase 6: Signal flow
    signal_flow = analyze_signal_flow(data, active_objects, signal_data)

    # Phase 7: Event pools and timers
    event_pools = analyze_event_pools_and_timers(data, all_strings)

    # Build summary
    prod_aos = [ao for ao in active_objects if not ao["is_test_ao"]]
    test_aos = [ao for ao in active_objects if ao["is_test_ao"]]

    rtos_summary = {
        "total_active_objects": len(active_objects),
        "production_aos": len(prod_aos),
        "test_aos": len(test_aos),
        "total_signals": signal_data["total_signals"],
        "signal_subsystems": len(signal_data["signal_counts"]),
        "top_signal_subsystems": dict(
            sorted(signal_data["signal_counts"].items(), key=lambda x: -x[1])[:10]
        ),
        "qp_framework_strings": qp_framework["total_qp_strings"],
        "state_machines_found": len(state_machines),
        "cross_subsystem_signals": len(signal_flow.get("cross_subsystem_signals", [])),
        "report_signal_count": len(signal_flow.get("report_signals", [])),
    }

    # Compile final output
    output = {
        "active_objects": active_objects,
        "signals": {k: v for k, v in signal_data["signals_by_subsystem"].items()},
        "signal_counts": signal_data["signal_counts"],
        "qp_framework": qp_framework,
        "systick": systick,
        "state_machines": state_machines,
        "signal_flow": signal_flow,
        "event_pools_and_timers": event_pools,
        "rtos_summary": rtos_summary,
    }

    # Save output
    save_output("track_e_rtos.json", output)

    # Print summary
    elapsed = time.time() - overall_start
    print("\n" + "=" * 70)
    print("RTOS Analysis Summary")
    print("=" * 70)
    print(f"  Production Active Objects:  {rtos_summary['production_aos']}")
    print(f"  Test Active Objects:        {rtos_summary['test_aos']}")
    print(f"  Total Signals:              {rtos_summary['total_signals']}")
    print(f"  Signal Subsystems:          {rtos_summary['signal_subsystems']}")
    print(f"  State Machines Found:       {rtos_summary['state_machines_found']}")
    print(f"  Cross-Subsystem Signals:    {rtos_summary['cross_subsystem_signals']}")
    print(f"  QP Framework Strings:       {rtos_summary['qp_framework_strings']}")
    print(f"  Total elapsed time:         {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
