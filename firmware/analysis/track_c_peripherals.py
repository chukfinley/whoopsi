#!/usr/bin/env python3
"""
Track C: Peripheral & Sensor Driver Analysis
=============================================
Scans Whoop 5.0 firmware (Apollo4 Blue Plus / ARM Cortex-M4F) for:
  1. MMIO peripheral register references
  2. I2C sensor device addresses
  3. Sensor driver init/read functions
  4. PPG/AFE optical driver code
  5. Interrupt handler vector table analysis

Usage:
    python3 track_c_peripherals.py
    python3 track_c_peripherals.py /path/to/firmware.bin

Output:
    analysis/output/track_c_peripherals.json
"""

import struct
import sys
import re
from pathlib import Path

# Ensure the analysis package is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))
from analysis.common import (
    load_firmware,
    get_capstone_md,
    disasm,
    disasm_function,
    find_function_start,
    find_function_prologs,
    find_peripheral_references,
    find_string_references,
    extract_strings,
    categorize_string,
    parse_vector_table,
    find_bl_targets,
    APOLLO4_PERIPHERALS,
    SENSOR_I2C_ADDRESSES,
    MRAM_BASE,
    VECTOR_TABLE_OFFSET,
    save_output,
    DEFAULT_BIN,
)


# ---------------------------------------------------------------------------
# Enhanced reference finder: literal pools + MOVW/MOVT pairs
# ---------------------------------------------------------------------------

def find_address_references(data: bytes, target_addr: int,
                            search_end: int = None) -> list:
    """Find code locations that reference a 32-bit address.

    Combines two strategies:
      1. Literal pool scan: the address as a 4-byte LE word (common.py approach)
      2. MOVW/MOVT instruction pairs: immediate split across two instructions
         MOVW Rd, #lower16  ->  MOVT Rd, #upper16
    """
    if search_end is None:
        search_end = len(data)
    refs = []

    # Strategy 1: Literal pool entries (word-aligned 4-byte LE)
    addr_bytes = struct.pack("<I", target_addr)
    off = 0
    while off < search_end:
        idx = data.find(addr_bytes, off, search_end)
        if idx < 0:
            break
        if idx % 4 == 0:
            refs.append({"offset": idx, "type": "literal_pool"})
        off = idx + 4

    # Strategy 2: MOVW encoding in Thumb-2
    # MOVW: 0xF240 | (i << 10) | imm4 ,  0x0000 | (imm3 << 12) | Rd<<8 | imm8
    # We search for the lower 16 bits loaded by MOVW
    lower16 = target_addr & 0xFFFF
    upper16 = (target_addr >> 16) & 0xFFFF

    # Encode MOVW imm16 fields
    def encode_movw_first_hw(imm16):
        """Return possible first halfwords for MOVW with given imm16."""
        imm4 = (imm16 >> 12) & 0xF
        i = (imm16 >> 11) & 1
        # First halfword: 0xF240 | (i << 10) | imm4
        # But also 0xF2C0 for MOVT
        hw1_movw = 0xF200 | (i << 10) | imm4
        hw1_movt = 0xF2C0 | (i << 10) | ((imm16 >> 12) & 0xF)
        return hw1_movw, hw1_movt

    # Search for MOVW with lower16: scan for the first halfword pattern
    movw_hw1, _ = encode_movw_first_hw(lower16)
    movw_bytes = struct.pack("<H", movw_hw1)
    off = 0
    code_end = min(search_end, 0x100000)  # Code region
    while off < code_end - 3:
        idx = data.find(movw_bytes, off, code_end)
        if idx < 0:
            break
        if idx % 2 == 0:
            # Verify second halfword matches remaining immediate bits
            hw2 = struct.unpack_from("<H", data, idx + 2)[0]
            imm3 = (lower16 >> 8) & 0x7
            imm8 = lower16 & 0xFF
            rd = (hw2 >> 8) & 0xF
            expected_hw2_base = (imm3 << 12) | imm8
            if (hw2 & 0x7F00FF) == (expected_hw2_base & 0x7F00FF):
                # Found a MOVW with our lower16 -- check if MOVT follows nearby
                # MOVT is typically within 2-6 bytes after MOVW
                for delta in range(4, 20, 2):
                    if idx + delta + 4 > code_end:
                        break
                    hw3 = struct.unpack_from("<H", data, idx + delta)[0]
                    i_t = (upper16 >> 11) & 1
                    imm4_t = (upper16 >> 12) & 0xF
                    expected_hw3 = 0xF2C0 | (i_t << 10) | imm4_t
                    if hw3 == expected_hw3:
                        hw4 = struct.unpack_from("<H", data, idx + delta + 2)[0]
                        imm3_t = (upper16 >> 8) & 0x7
                        imm8_t = upper16 & 0xFF
                        expected_hw4_base = (imm3_t << 12) | imm8_t
                        if (hw4 & 0x7F00FF) == (expected_hw4_base & 0x7F00FF):
                            refs.append({
                                "offset": idx,
                                "type": "movw_movt",
                                "movt_offset": idx + delta,
                            })
                            break
        off = idx + 2

    return refs


# ---------------------------------------------------------------------------
# 1. Apollo4 MMIO Register Map scan
# ---------------------------------------------------------------------------

def scan_peripheral_references(data: bytes) -> dict:
    """Scan firmware for all Apollo4 peripheral base address references."""
    print("[1/5] Scanning MMIO peripheral references...")
    results = {}
    md = get_capstone_md()
    code_end = min(len(data), 0x0A0000)

    for periph_addr, periph_name in sorted(APOLLO4_PERIPHERALS.items()):
        refs = find_address_references(data, periph_addr, search_end=len(data))
        if not refs:
            continue

        entries = []
        for ref in refs:
            ref_offset = ref["offset"]
            in_code_region = ref_offset < code_end

            func_addr = None
            context_disasm = ""
            if in_code_region:
                func_addr = find_function_start(data, ref_offset)
                if func_addr is not None:
                    try:
                        instrs = disasm(data, func_addr, length=64, md=md)
                        context_disasm = "; ".join(
                            f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                            for ins in instrs[:6]
                        )
                    except Exception:
                        pass

            entry = {
                "offset": ref_offset,
                "ref_type": ref["type"],
                "in_code_region": in_code_region,
            }
            if func_addr is not None:
                entry["function_start"] = func_addr
            if context_disasm:
                entry["context"] = context_disasm

            entries.append(entry)

        results[periph_name] = {
            "base_address": f"0x{periph_addr:08X}",
            "reference_count": len(entries),
            "references": entries[:30],
        }
        print(f"  {periph_name} (0x{periph_addr:08X}): {len(entries)} refs")

    return results


# ---------------------------------------------------------------------------
# 2. I2C device address scan
# ---------------------------------------------------------------------------

def scan_i2c_device_addresses(data: bytes) -> list:
    """Search for known sensor I2C addresses as immediate values in the binary."""
    print("[2/5] Scanning I2C device addresses...")
    md = get_capstone_md()
    code_end = min(len(data), 0x0A0000)
    results = []

    for i2c_addr, device_name in sorted(SENSOR_I2C_ADDRESSES.items()):
        code_refs = []

        # Search values: raw address and shifted-left (7-bit addr in 8-bit field)
        search_values = [i2c_addr, i2c_addr << 1]

        for search_val in search_values:
            if search_val > 0xFF:
                continue

            # Thumb MOVS Rd, #imm8: encoding 0x20xx-0x27xx
            for reg in range(8):
                pattern = struct.pack("<H", 0x2000 | (reg << 8) | search_val)
                off = 0
                while off < code_end - 1:
                    idx = data.find(pattern, off, code_end)
                    if idx < 0:
                        break
                    if idx % 2 == 0:
                        func_start = find_function_start(data, idx)
                        code_refs.append({
                            "code_offset": idx,
                            "instruction": f"movs r{reg}, #0x{search_val:02X}",
                            "value_used": f"0x{search_val:02X}",
                            "function_start": func_start,
                        })
                    off = idx + 2

        # Literal pool as 32-bit word
        for search_val in search_values:
            addr_bytes = struct.pack("<I", search_val)
            off = 0
            while off < code_end:
                idx = data.find(addr_bytes, off, code_end)
                if idx < 0:
                    break
                if idx % 4 == 0:
                    func_start = find_function_start(data, idx)
                    code_refs.append({
                        "code_offset": idx,
                        "instruction": "literal_pool",
                        "value_used": f"0x{search_val:08X}",
                        "function_start": func_start,
                    })
                off = idx + 4

        # Deduplicate
        seen = set()
        unique_refs = []
        for ref in code_refs:
            if ref["code_offset"] not in seen:
                seen.add(ref["code_offset"])
                unique_refs.append(ref)

        if unique_refs:
            results.append({
                "name": device_name,
                "address": f"0x{i2c_addr:02X}",
                "address_decimal": i2c_addr,
                "code_refs": unique_refs[:20],
                "total_refs": len(unique_refs),
            })
            print(f"  {device_name} (0x{i2c_addr:02X}): {len(unique_refs)} code refs")
        else:
            print(f"  {device_name} (0x{i2c_addr:02X}): not found")

    return results


# ---------------------------------------------------------------------------
# 3. Sensor driver function identification
# ---------------------------------------------------------------------------

def _find_all_string_refs(data: bytes, string_offset: int) -> list:
    """Enhanced string reference finder using both literal pools and full scan."""
    # Use common.py's version with expanded range, plus our own scan
    refs = find_string_references(data, string_offset,
                                  search_range=(0, len(data)))
    # Also look via our address reference finder
    target_addr = MRAM_BASE + string_offset
    extra = find_address_references(data, target_addr,
                                    search_end=min(len(data), 0x100000))
    for e in extra:
        if e["offset"] not in refs:
            refs.append(e["offset"])
    return refs


def find_sensor_drivers(data: bytes, strings: list) -> list:
    """Identify sensor driver functions from source file string references."""
    print("[3/5] Identifying sensor driver functions...")
    md = get_capstone_md()

    driver_patterns = [
        ("sensors_ao", re.compile(r"sensors_ao\.c", re.IGNORECASE)),
        ("imu_driver", re.compile(r"imu|icm|accel|gyro", re.IGNORECASE)),
        ("ppg_driver", re.compile(r"ppg|afe|optical", re.IGNORECASE)),
        ("temp_sensor", re.compile(r"temp_sensor|as6221|therm", re.IGNORECASE)),
        ("led_driver", re.compile(r"lp5562|led_driv", re.IGNORECASE)),
        ("haptic_driver", re.compile(r"drv2625|haptic", re.IGNORECASE)),
        ("fuel_gauge", re.compile(r"fuel.*gauge|lc709|battery_ao", re.IGNORECASE)),
        ("sigproc", re.compile(r"sigproc", re.IGNORECASE)),
    ]

    driver_strings = {}
    for s in strings:
        for driver_name, pattern in driver_patterns:
            if pattern.search(s["text"]):
                if driver_name not in driver_strings:
                    driver_strings[driver_name] = []
                driver_strings[driver_name].append(s)

    results = []
    for driver_name, matched_strings in driver_strings.items():
        driver_entry = {
            "name": driver_name,
            "string_refs": [],
            "functions": [],
        }

        known_func_addrs = set()
        for s in matched_strings[:15]:
            string_offset = s["offset"]
            driver_entry["string_refs"].append({
                "text": s["text"][:120],
                "offset": string_offset,
            })

            code_refs = _find_all_string_refs(data, string_offset)
            for cr in code_refs[:5]:
                cr_off = cr if isinstance(cr, int) else cr
                func_start = find_function_start(data, cr_off)
                if func_start is not None and func_start not in known_func_addrs:
                    known_func_addrs.add(func_start)
                    try:
                        instrs = disasm_function(data, func_start,
                                                 max_insns=40, md=md)
                        insn_text = [
                            f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                            for ins in instrs[:12]
                        ]

                        has_bl_call = any(
                            ins.mnemonic in ("bl", "bl.w")
                            for ins in instrs
                        )

                        driver_entry["functions"].append({
                            "address": func_start,
                            "referencing_literal_pool": cr_off,
                            "prologue": insn_text,
                            "has_bl_call": has_bl_call,
                        })
                    except Exception:
                        pass

        results.append(driver_entry)
        func_count = len(driver_entry["functions"])
        str_count = len(driver_entry["string_refs"])
        print(f"  {driver_name}: {str_count} strings, {func_count} functions")

    return results


# ---------------------------------------------------------------------------
# 4. PPG / AFE driver analysis
# ---------------------------------------------------------------------------

def analyze_ppg_afe(data: bytes, strings: list) -> dict:
    """Deep analysis of PPG/AFE (optical heart rate / SpO2) driver code."""
    print("[4/5] Analyzing PPG/AFE driver code...")
    md = get_capstone_md()

    ppg_keywords = [
        "ppg", "afe", "optical", "spo2", "heart", "sigproc",
        "red", "infrared", "led_current", "photodiode",
        "oxygen", "saturation", "pulse",
    ]

    string_refs = []
    code_refs_set = set()
    code_refs = []

    for s in strings:
        text_lower = s["text"].lower()
        matched_keywords = [kw for kw in ppg_keywords if kw in text_lower]
        if not matched_keywords:
            continue

        string_refs.append({
            "text": s["text"][:150],
            "offset": s["offset"],
            "keywords": matched_keywords,
        })

        crefs = _find_all_string_refs(data, s["offset"])
        for cr in crefs[:3]:
            cr_off = cr if isinstance(cr, int) else cr
            func_start = find_function_start(data, cr_off)
            if func_start is not None and func_start not in code_refs_set:
                code_refs_set.add(func_start)
                try:
                    instrs = disasm_function(data, func_start,
                                             max_insns=60, md=md)
                    insn_text = [
                        f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                        for ins in instrs[:20]
                    ]

                    fpu_count = sum(
                        1 for ins in instrs
                        if ins.mnemonic.startswith("v")
                    )

                    code_refs.append({
                        "function_start": func_start,
                        "literal_pool_ref": cr_off,
                        "fpu_instruction_count": fpu_count,
                        "disassembly": insn_text,
                    })
                except Exception:
                    pass

    code_refs.sort(key=lambda x: x["fpu_instruction_count"], reverse=True)

    print(f"  PPG/AFE strings: {len(string_refs)}")
    print(f"  PPG/AFE functions: {len(code_refs)}")
    if code_refs:
        top_fpu = code_refs[0]
        print(f"  Most FPU-heavy function: 0x{top_fpu['function_start']:06X} "
              f"({top_fpu['fpu_instruction_count']} FPU insns)")

    return {
        "string_refs": string_refs[:60],
        "code_refs": code_refs[:40],
        "total_string_refs": len(string_refs),
        "total_code_refs": len(code_refs),
    }


# ---------------------------------------------------------------------------
# 5. Interrupt handler vector table analysis
# ---------------------------------------------------------------------------

def analyze_interrupt_handlers(data: bytes) -> list:
    """Parse vector table and classify dedicated vs default handlers."""
    print("[5/5] Analyzing interrupt handler vector table...")
    md = get_capstone_md()

    vectors = parse_vector_table(data, VECTOR_TABLE_OFFSET)

    addr_counts = {}
    for name, addr in vectors.items():
        if addr != 0:
            addr_counts[addr] = addr_counts.get(addr, 0) + 1
    default_handler = max(addr_counts, key=addr_counts.get) if addr_counts else 0

    results = []
    dedicated_count = 0

    vector_names = [
        "SP_Init", "Reset_Handler", "NMI_Handler", "HardFault_Handler",
        "MemManage_Handler", "BusFault_Handler", "UsageFault_Handler",
        "Reserved_7", "Reserved_8", "Reserved_9", "Reserved_10",
        "SVC_Handler", "DebugMon_Handler", "Reserved_13",
        "PendSV_Handler", "SysTick_Handler",
    ]
    for i in range(48):
        vector_names.append(f"IRQ{i}_Handler")

    for i, name in enumerate(vector_names):
        table_offset = VECTOR_TABLE_OFFSET + i * 4
        if table_offset + 4 > len(data):
            break
        addr = struct.unpack_from("<I", data, table_offset)[0]

        is_dedicated = addr != 0 and addr != default_handler
        if is_dedicated and name.startswith("IRQ"):
            dedicated_count += 1

        entry = {
            "index": i,
            "name": name,
            "address": f"0x{addr:08X}",
            "address_int": addr,
            "is_dedicated": is_dedicated,
            "is_default_handler": addr == default_handler,
        }

        if is_dedicated and addr != 0:
            file_offset = (addr & ~1) - MRAM_BASE
            if 0 <= file_offset < len(data) - 16:
                try:
                    instrs = disasm(data, file_offset, length=32, md=md)
                    entry["prologue"] = [
                        f"0x{ins.address:06X}: {ins.mnemonic} {ins.op_str}"
                        for ins in instrs[:6]
                    ]
                except Exception:
                    pass

        results.append(entry)

    print(f"  Default handler: 0x{default_handler:08X}")
    print(f"  Dedicated IRQ handlers: {dedicated_count}")
    print(f"  Total vectors parsed: {len(results)}")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Track C: Peripheral & Sensor Driver Analysis")
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

    peripheral_refs = scan_peripheral_references(data)
    print()

    i2c_devices = scan_i2c_device_addresses(data)
    print()

    sensor_drivers = find_sensor_drivers(data, strings)
    print()

    ppg_afe = analyze_ppg_afe(data, strings)
    print()

    interrupt_handlers = analyze_interrupt_handlers(data)
    print()

    # Build summary
    total_periph_refs = sum(
        p["reference_count"] for p in peripheral_refs.values()
    )
    unique_peripherals = len(peripheral_refs)
    dedicated_irqs = sum(
        1 for h in interrupt_handlers
        if h["is_dedicated"] and h["name"].startswith("IRQ")
    )

    summary = {
        "total_peripheral_refs": total_periph_refs,
        "unique_peripherals": unique_peripherals,
        "i2c_devices_found": len(i2c_devices),
        "sensor_drivers_identified": len(sensor_drivers),
        "ppg_afe_functions": ppg_afe["total_code_refs"],
        "ppg_afe_strings": ppg_afe["total_string_refs"],
        "dedicated_irq_handlers": dedicated_irqs,
        "total_vectors": len(interrupt_handlers),
    }

    output = {
        "peripheral_references": peripheral_refs,
        "i2c_devices": i2c_devices,
        "sensor_drivers": sensor_drivers,
        "ppg_afe": ppg_afe,
        "interrupt_handlers": interrupt_handlers,
        "peripheral_summary": summary,
    }

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print()

    save_output("track_c_peripherals.json", output)
    print()
    print("Done.")


if __name__ == "__main__":
    main()
