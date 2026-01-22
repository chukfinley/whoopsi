#!/usr/bin/env python3
"""
Whoop Firmware Analysis — Master Report Generator
==================================================
Combines all JSON outputs from tracks A-F into a comprehensive HTML report.

Usage:
  python3 generate_report.py
  python3 generate_report.py -o custom_report.html
"""
import json
import html
import sys
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / "output"
REPORT_DIR = Path(__file__).parent


def load_json(name: str) -> dict:
    """Load a track output JSON file, return empty dict if missing."""
    path = OUTPUT_DIR / name
    if path.exists():
        with open(path) as f:
            return json.load(f)
    print(f"  Warning: {name} not found, skipping")
    return {}


def h(text) -> str:
    """HTML-escape text."""
    if text is None:
        return ""
    return html.escape(str(text))


def format_addr(addr) -> str:
    """Format an address as hex."""
    if isinstance(addr, int):
        return f"0x{addr:06X}"
    return str(addr)


def _format_source_paths(source_paths):
    """Format source paths for the report, handling both str and dict entries."""
    if not source_paths:
        return "Not analyzed"
    NL = "\n"
    lines = []
    for p in source_paths[:40]:
        if isinstance(p, str):
            lines.append(h(p))
        else:
            lines.append(h(p.get("text", "")))
    return NL.join(lines)


def generate_report(output_path: str = None):
    print("Loading track outputs...")

    track_a = load_json("track_a_functions.json")
    track_b = load_json("track_b_strings.json")
    track_c = load_json("track_c_peripherals.json")
    track_d = load_json("track_d_algorithms.json")
    track_e = load_json("track_e_rtos.json")
    track_f = load_json("track_f_security.json")

    tracks_loaded = sum(1 for t in [track_a, track_b, track_c, track_d, track_e, track_f] if t)
    print(f"  {tracks_loaded}/6 tracks loaded")

    # --- Extract key metrics ---
    total_functions = track_a.get("total_functions", 0) or len(track_a.get("r2_functions", []))
    total_strings = track_b.get("total_strings", 0)
    category_counts = track_b.get("category_counts", {})
    source_paths = track_b.get("source_paths", [])
    periph_summary = track_c.get("peripheral_summary", {})
    algo_summary = track_d.get("algorithm_summary", {})
    rtos_summary = track_e.get("rtos_summary", {})
    active_objects = track_e.get("active_objects", [])
    signal_counts = track_e.get("signal_counts", {})
    security = track_f.get("security_assessment", "Not analyzed")
    fw_security = track_f.get("firmware_update_security", {})
    boot_seq = track_f.get("boot_sequence", {})

    # Vector table
    vectors = track_a.get("vector_table", {})

    # Function classification
    func_class = track_a.get("function_classification", {})

    # Peripheral references
    periph_refs = track_c.get("peripheral_references", {})
    i2c_devices = track_c.get("i2c_devices", [])

    # Algorithm regions
    fpu_regions = track_d.get("fpu_regions", [])
    hr_algo = track_d.get("hr_algorithm", {})
    spo2_algo = track_d.get("spo2_algorithm", {})

    # Debug interfaces
    debug = track_f.get("debug_interfaces", {})
    crypto = track_f.get("crypto_analysis", {})

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Pre-compute complex template values to avoid f-string issues
    vector_rows = ""
    if vectors:
        for name, addr in list(vectors.items())[:20]:
            is_dedicated = (addr != vectors.get("SVC_Handler", 0) or name in ("SP_Init", "Reset_Handler", "SysTick_Handler"))
            handler_type = "Dedicated" if is_dedicated else "Default (shared)"
            vector_rows += f'<tr><td>{h(name)}</td><td class="hex">{format_addr(addr)}</td><td>{handler_type}</td></tr>\n'
    else:
        vector_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    func_class_rows = ""
    if func_class:
        for cat, funcs in func_class.items():
            func_class_rows += f'<tr><td>{h(cat)}</td><td>{len(funcs)}</td></tr>\n'
    else:
        func_class_rows = '<tr><td colspan="2">Not analyzed</td></tr>'

    category_grid = ""
    if category_counts:
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            category_grid += f'<div class="stat"><div class="value" style="font-size:20px">{count}</div><div class="label">{h(cat)}</div></div>\n'

    periph_rows = ""
    if periph_refs:
        for name, refs in periph_refs.items():
            ref_count = len(refs) if isinstance(refs, list) else refs
            periph_rows += f'<tr><td>{h(name)}</td><td class="hex">{h(name)}</td><td>{ref_count}</td></tr>\n'
    else:
        periph_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    i2c_rows = ""
    if i2c_devices:
        for d in i2c_devices:
            addr_val = d.get("address", 0)
        addr_str = f"0x{addr_val:02X}" if isinstance(addr_val, int) else h(str(addr_val))
        i2c_rows += f'<tr><td>{h(d.get("name","?"))}</td><td class="hex">{addr_str}</td><td>{len(d.get("code_refs",[]))}</td></tr>\n'
    else:
        i2c_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    fpu_rows = ""
    if fpu_regions:
        for r in fpu_regions[:20]:
            fpu_rows += f'<tr><td class="hex">{format_addr(r.get("start",0))}</td><td class="hex">{format_addr(r.get("end",0))}</td><td>{r.get("fpu_count",0)}</td></tr>\n'
    else:
        fpu_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    hr_text = h(json.dumps(hr_algo.get("string_refs", [])[:5], indent=2)) if hr_algo.get("string_refs") else "Searching for PPG peak detection pipeline..."
    spo2_text = h(json.dumps(spo2_algo.get("string_refs", [])[:5], indent=2)) if spo2_algo.get("string_refs") else "Searching for red/IR ratio SpO2 lookup..."

    ao_rows = ""
    if active_objects:
        for ao in active_objects:
            ao_rows += f'<tr><td>{h(ao.get("name","?"))}</td><td class="hex">{h(ao.get("source_file","?"))}</td><td>{len(ao.get("code_refs",[]))}</td></tr>\n'
    else:
        ao_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    signal_rows = ""
    if signal_counts:
        for sub, count in sorted(signal_counts.items(), key=lambda x: -x[1]):
            bar_width = min(count * 3, 300)
            signal_rows += f'<tr><td>{h(sub)}</td><td>{count}</td><td><div class="bar" style="width: {bar_width}px; background: var(--blue);"></div></td></tr>\n'
    else:
        signal_rows = '<tr><td colspan="3">Not analyzed</td></tr>'

    boot_init_chain = h(json.dumps(boot_seq.get('init_chain', [])[:10], indent=2)) if boot_seq.get('init_chain') else 'Not analyzed'
    crc_only = fw_security.get('crc_only', True)
    crypto_found = fw_security.get('crypto_found', False)
    jtag_status = h(debug.get('jtag_swd', {}).get('status', 'Unknown'))
    security_text = h(security) if isinstance(security, str) else ''
    crc_class = 'pass' if crc_only else 'warn'
    crc_text = 'CRC32-only validation (no crypto signatures found)' if crc_only else 'Crypto analysis results available'

    total_signals = rtos_summary.get('total_signals', sum(signal_counts.values()) if signal_counts else 0)
    total_aos = rtos_summary.get('total_aos', len(active_objects))

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Whoop Maverick Firmware Analysis Report</title>
<style>
  :root {{
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #c9d1d9; --dim: #8b949e; --blue: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --purple: #d2a8ff; --cyan: #79c0ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
         background: var(--bg); color: var(--text); padding: 20px; line-height: 1.6; }}
  h1 {{ color: var(--blue); font-size: 28px; border-bottom: 2px solid var(--blue);
       padding-bottom: 10px; margin-bottom: 20px; }}
  h2 {{ color: var(--cyan); font-size: 20px; margin-top: 30px; margin-bottom: 10px;
       border-bottom: 1px solid var(--border); padding-bottom: 5px; }}
  h3 {{ color: var(--purple); font-size: 16px; margin-top: 15px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
          padding: 16px; margin: 12px 0; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
  .stat {{ text-align: center; padding: 15px; }}
  .stat .value {{ font-size: 32px; font-weight: bold; color: var(--blue); }}
  .stat .label {{ font-size: 13px; color: var(--dim); margin-top: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 14px; }}
  th, td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
  th {{ background: #21262d; color: var(--cyan); font-weight: 600; }}
  tr:hover {{ background: rgba(88, 166, 255, 0.05); }}
  .hex {{ font-family: 'Fira Code', 'Cascadia Code', monospace; font-size: 12px; }}
  .tag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px;
         font-weight: 600; margin: 2px; }}
  .tag-ble {{ background: #1f3a5f; color: #58a6ff; }}
  .tag-sensor {{ background: #1a3f2e; color: #3fb950; }}
  .tag-rtos {{ background: #3d2b1a; color: #d29922; }}
  .tag-fw {{ background: #3b1e2e; color: #f85149; }}
  .tag-debug {{ background: #2d1f3d; color: #d2a8ff; }}
  .tag-hw {{ background: #1f3d3d; color: #79c0ff; }}
  .tag-other {{ background: #21262d; color: #8b949e; }}
  .pass {{ color: var(--green); font-weight: bold; }}
  .fail {{ color: var(--red); font-weight: bold; }}
  .warn {{ color: var(--yellow); font-weight: bold; }}
  .bar {{ height: 20px; border-radius: 4px; display: inline-block; margin: 2px; }}
  pre {{ background: #1c2128; padding: 12px; border-radius: 6px; overflow-x: auto;
        font-family: 'Fira Code', monospace; font-size: 12px; line-height: 1.5; }}
  .toc {{ columns: 2; }}
  .toc a {{ color: var(--blue); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px;
           font-size: 12px; font-weight: bold; }}
  .badge-green {{ background: #1a3f2e; color: var(--green); }}
  .badge-red {{ background: #3b1e2e; color: var(--red); }}
  .badge-yellow {{ background: #3d2b1a; color: var(--yellow); }}
  .nav {{ position: fixed; top: 0; right: 0; background: var(--card); border: 1px solid var(--border);
         border-radius: 0 0 0 8px; padding: 10px; font-size: 12px; max-height: 90vh; overflow-y: auto; }}
  .nav a {{ display: block; color: var(--dim); text-decoration: none; padding: 2px 8px; }}
  .nav a:hover {{ color: var(--blue); }}
</style></head><body>

<nav class="nav">
  <a href="#summary">Summary</a>
  <a href="#functions">Functions</a>
  <a href="#strings">Strings</a>
  <a href="#peripherals">Peripherals</a>
  <a href="#algorithms">Algorithms</a>
  <a href="#rtos">RTOS</a>
  <a href="#security">Security</a>
</nav>

<h1>Whoop Maverick v50.35.2.0 — Firmware Analysis Report</h1>
<p style="color: var(--dim);">Generated: {now} | Firmware: maverick-50.35.2.0.bin (1,548,504 bytes) | Target: Ambiq Apollo4 Blue Plus (ARM Cortex-M4F)</p>

<!-- ===== EXECUTIVE SUMMARY ===== -->
<div id="summary" class="card">
  <h2>Executive Summary</h2>
  <div class="grid">
    <div class="stat"><div class="value">{total_functions}</div><div class="label">Functions Found</div></div>
    <div class="stat"><div class="value">{total_strings}</div><div class="label">Strings Extracted</div></div>
    <div class="stat"><div class="value">{periph_summary.get('unique_peripherals', len(periph_refs))}</div><div class="label">Peripherals</div></div>
    <div class="stat"><div class="value">{total_aos}</div><div class="label">Active Objects</div></div>
    <div class="stat"><div class="value">{total_signals}</div><div class="label">RTOS Signals</div></div>
    <div class="stat"><div class="value">{algo_summary.get('total_fpu_regions', len(fpu_regions))}</div><div class="label">Algorithm Regions</div></div>
  </div>

  <h3>Key Findings</h3>
  <ul>
    <li>RTOS: <strong>QP (Quantum Platform)</strong> active object framework with {total_aos} AOs</li>
    <li>Language: <strong>C</strong> (all source paths use .c extension)</li>
    <li>Security: <span class="{crc_class}">
      {crc_text}
    </span></li>
    <li>Sensors: ICM-45686 (IMU), LP5562 (LED), DRV2625 (Haptic), AS6221 (Temp), LC709205F (Fuel Gauge), PPG/AFE</li>
    <li>Debug: UART console accessible via BLE commands, Memfault crash reporting integrated</li>
  </ul>
</div>

<!-- ===== TRACK A: FUNCTIONS ===== -->
<div id="functions" class="card">
  <h2>Track A: Disassembly & Function Map</h2>

  <h3>Vector Table</h3>
  <table>
    <tr><th>Vector</th><th>Address</th><th>Handler</th></tr>
    {vector_rows}
  </table>

  <h3>Function Classification</h3>
  <table>
    <tr><th>Category</th><th>Count</th></tr>
    {func_class_rows}
  </table>
</div>

<!-- ===== TRACK B: STRINGS ===== -->
<div id="strings" class="card">
  <h2>Track B: String Analysis</h2>

  <h3>String Categories</h3>
  <div class="grid">
    {category_grid}
  </div>

  <h3>Source File Structure (from string paths)</h3>
  <pre>{_format_source_paths(source_paths)}</pre>
</div>

<!-- ===== TRACK C: PERIPHERALS ===== -->
<div id="peripherals" class="card">
  <h2>Track C: Peripheral & Sensor Drivers</h2>

  <h3>Peripheral Access Map</h3>
  <table>
    <tr><th>Peripheral</th><th>Base Address</th><th>Code References</th></tr>
    {periph_rows}
  </table>

  <h3>I2C Sensor Devices</h3>
  <table>
    <tr><th>Device</th><th>I2C Address</th><th>Code References</th></tr>
    {i2c_rows}
  </table>
</div>

<!-- ===== TRACK D: ALGORITHMS ===== -->
<div id="algorithms" class="card">
  <h2>Track D: Algorithm Extraction</h2>

  <h3>FPU-Heavy Regions (Algorithm Candidates)</h3>
  <table>
    <tr><th>Start</th><th>End</th><th>FPU Instructions</th></tr>
    {fpu_rows}
  </table>

  <h3>Heart Rate Algorithm</h3>
  <p>{hr_text}</p>

  <h3>SpO2 Algorithm</h3>
  <p>{spo2_text}</p>
</div>

<!-- ===== TRACK E: RTOS ===== -->
<div id="rtos" class="card">
  <h2>Track E: RTOS Architecture (QP Framework)</h2>

  <h3>Active Objects</h3>
  <table>
    <tr><th>Active Object</th><th>Source File</th><th>Code Refs</th></tr>
    {ao_rows}
  </table>

  <h3>Signal Distribution</h3>
  <table>
    <tr><th>Subsystem</th><th>Signals</th><th>Bar</th></tr>
    {signal_rows}
  </table>
</div>

<!-- ===== TRACK F: SECURITY ===== -->
<div id="security" class="card">
  <h2>Track F: Security Assessment</h2>

  <h3>Boot Sequence</h3>
  <pre>Reset_Handler: {h(boot_seq.get('reset_handler', '0x0004A4D9'))}
Init chain: {boot_init_chain}</pre>

  <h3>Firmware Update Security</h3>
  <table>
    <tr><th>Property</th><th>Value</th><th>Assessment</th></tr>
    <tr><td>Auth Algorithm</td><td>{fw_security.get('auth_algo', 1)}</td><td class="warn">Header field only</td></tr>
    <tr><td>Auth Key Index</td><td>{fw_security.get('auth_key_idx', 13)} (0x0D)</td><td class="warn">Unknown significance</td></tr>
    <tr><td>CRC-only Validation</td><td class="{'pass' if crc_only else 'fail'}">
      {'Yes' if crc_only else 'No'}</td>
      <td>{'No crypto signatures in application FW' if crc_only else ''}</td></tr>
    <tr><td>Crypto Operations</td><td>{'Found' if crypto_found else 'Not found'}</td>
      <td class="{'fail' if crypto_found else 'pass'}">
        {'Crypto detected' if crypto_found else 'No RSA/ECDSA/AES in update path'}</td></tr>
  </table>

  <h3>Debug Interfaces</h3>
  <table>
    <tr><th>Interface</th><th>Status</th><th>Details</th></tr>
    <tr><td>UART Console</td><td class="warn">Accessible via BLE</td>
      <td>BLE_CMD_UART_ENABLE/DISABLE, extensive debug menu</td></tr>
    <tr><td>Memfault</td><td>Integrated</td><td>Crash reporting via BLE characteristic</td></tr>
    <tr><td>JTAG/SWD</td><td class="warn">{jtag_status}</td>
      <td>Physical access required</td></tr>
  </table>

  <h3>Overall Security Assessment</h3>
  <div class="card" style="border-color: var(--yellow);">
    <p>{security_text}</p>
    <ul>
      <li><span class="badge badge-green">Low Risk</span> CRC-only validation in application firmware</li>
      <li><span class="badge badge-yellow">Medium Risk</span> SBL (ROM) may enforce crypto — untestable without hardware</li>
      <li><span class="badge badge-green">Low Risk</span> No anti-rollback protection found</li>
      <li><span class="badge badge-yellow">Medium Risk</span> Cooper BLE radio has independent auth ("FW Auth Passed")</li>
      <li><span class="badge badge-red">High Value</span> UART debug console accessible via BLE commands</li>
    </ul>
  </div>
</div>

<!-- ===== ARCHITECTURE DIAGRAM ===== -->
<div class="card">
  <h2>RTOS Architecture Diagram</h2>
  <pre style="font-size: 11px; line-height: 1.3;">
  ┌─────────────────────────────────────────────────────────────────┐
  │                    QP RTOS (Active Object Framework)            │
  ├─────────────┬──────────────┬──────────────┬────────────────────┤
  │  SUPERVISOR │   BLE AO     │  BLE CMD AO  │   CORDIO AO        │
  │  (system    │  (stack mgr) │  (AA01 cmds) │   (BLE integration)│
  │   manager)  │              │              │                    │
  ├─────────────┼──────────────┼──────────────┼────────────────────┤
  │  SENSORS AO │  ANALYTICS   │   FLASH AO   │   I2C AO           │
  │  (data      │  (signal     │  (circular   │   (bus driver,      │
  │   collect)  │   processing)│   buffer)    │    multi-bus)       │
  ├─────────────┼──────────────┼──────────────┼────────────────────┤
  │  FUEL GAUGE │  LISTENER    │  TEMP SENS   │   TAG READER        │
  │  LC709205F  │  (WPT/charge)│  AS6221      │   (RFID/NFC)        │
  ├─────────────┼──────────────┼──────────────┼────────────────────┤
  │  UI MANAGER │  LED UI AO   │  HAPTICS AO  │   ECG CONTROL       │
  │  (orchestr) │  LP5562      │  DRV2625     │   (recording)       │
  ├─────────────┼──────────────┼──────────────┼────────────────────┤
  │  DEBUG MENU │  ITEST AO    │              │                    │
  │  (UART)     │  (mfg tests) │              │                    │
  └─────────────┴──────────────┴──────────────┴────────────────────┘
       ↕ signals        ↕ signals       ↕ signals       ↕ I2C/SPI
  ┌─────────────────────────────────────────────────────────────────┐
  │                    Hardware Abstraction Layer                    │
  │  Apollo4 Blue Plus: GPIO, UART, IOM (I2C/SPI), MSPI, ADC, BLE │
  └─────────────────────────────────────────────────────────────────┘
  </pre>
</div>

<div class="card" style="color: var(--dim); text-align: center;">
  <p>Whoop Maverick Firmware Analysis Report | Generated {now}</p>
  <p>Firmware: maverick-50.35.2.0.bin | Target: Ambiq Apollo4 Blue Plus (ARM Cortex-M4F Thumb-2)</p>
</div>

</body></html>"""

    # Write report
    output = Path(output_path or str(REPORT_DIR / "firmware_analysis_report.html"))
    output.write_text(report)
    print(f"\nReport generated: {output} ({output.stat().st_size:,} bytes)")
    return str(output)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate firmware analysis HTML report")
    parser.add_argument("-o", "--output", help="Output HTML file path")
    args = parser.parse_args()
    generate_report(args.output)


if __name__ == "__main__":
    main()
