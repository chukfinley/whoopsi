#!/usr/bin/env python3
"""
Whoop Firmware Diff Tool
========================
Compare two firmware .bin files: byte-level diff, function diff, string diff.
Generates an HTML report with change highlights.

Usage:
  python3 firmware_diff.py old.bin new.bin
  python3 firmware_diff.py old.bin new.bin -o diff_report.html
"""
import struct
import hashlib
import argparse
import sys
import html
from pathlib import Path
from datetime import datetime


def extract_strings(data: bytes, min_length: int = 6) -> dict:
    """Extract ASCII strings and their offsets."""
    strings = {}
    current = b""
    current_start = 0
    for i in range(len(data)):
        b = data[i]
        if 0x20 <= b <= 0x7E:
            if not current:
                current_start = i
            current += bytes([b])
        else:
            if len(current) >= min_length:
                text = current.decode('ascii', errors='replace')
                strings[current_start] = text
            current = b""
    if len(current) >= min_length:
        strings[current_start] = current.decode('ascii', errors='replace')
    return strings


def find_function_prologs(data: bytes) -> list:
    """Find ARM Thumb-2 PUSH {.., lr} prologs."""
    prologs = []
    for off in range(0, len(data) - 1, 2):
        hw = struct.unpack_from("<H", data, off)[0]
        if (hw & 0xFF00) == 0xB500:
            prologs.append(off)
        elif hw == 0xE92D and off + 2 < len(data):
            hw2 = struct.unpack_from("<H", data, off + 2)[0]
            if hw2 & 0x4000:
                prologs.append(off)
    return prologs


def diff_bytes(old: bytes, new: bytes) -> list:
    """Find byte-level differences between two binaries."""
    diffs = []
    min_len = min(len(old), len(new))
    current_diff = None

    for i in range(min_len):
        if old[i] != new[i]:
            if current_diff is None:
                current_diff = {"offset": i, "old": [], "new": []}
            current_diff["old"].append(old[i])
            current_diff["new"].append(new[i])
        else:
            if current_diff is not None:
                current_diff["length"] = len(current_diff["old"])
                diffs.append(current_diff)
                current_diff = None

    if current_diff is not None:
        current_diff["length"] = len(current_diff["old"])
        diffs.append(current_diff)

    # Handle size difference
    if len(old) != len(new):
        diffs.append({
            "type": "size_change",
            "old_size": len(old),
            "new_size": len(new),
            "delta": len(new) - len(old),
        })

    return diffs


def diff_strings(old_data: bytes, new_data: bytes) -> dict:
    """Compare strings between two firmware versions."""
    old_strings = extract_strings(old_data)
    new_strings = extract_strings(new_data)

    old_set = set(old_strings.values())
    new_set = set(new_strings.values())

    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    common = sorted(old_set & new_set)

    return {
        "added": added,
        "removed": removed,
        "common_count": len(common),
        "old_total": len(old_strings),
        "new_total": len(new_strings),
    }


def diff_functions(old_data: bytes, new_data: bytes) -> dict:
    """Compare function prologs between two versions."""
    old_prologs = set(find_function_prologs(old_data))
    new_prologs = set(find_function_prologs(new_data))

    added = sorted(new_prologs - old_prologs)
    removed = sorted(old_prologs - new_prologs)
    common = sorted(old_prologs & new_prologs)

    # Check which common functions have changed code
    changed = []
    for addr in common:
        # Compare 256 bytes starting from each function
        end = min(addr + 256, len(old_data), len(new_data))
        if old_data[addr:end] != new_data[addr:end]:
            changed.append(addr)

    return {
        "added": added[:100],
        "removed": removed[:100],
        "changed": changed[:100],
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "common_count": len(common),
        "old_total": len(old_prologs),
        "new_total": len(new_prologs),
    }


def parse_header(data: bytes) -> dict:
    """Parse firmware .bin header."""
    if len(data) < 0x200:
        return {}
    magic = struct.unpack_from('<I', data, 0x000)[0]
    ver_major = struct.unpack_from('<I', data, 0x07C)[0]
    ver_minor = struct.unpack_from('<I', data, 0x080)[0]
    ver_patch = struct.unpack_from('<I', data, 0x084)[0]
    build_info = data[0x018:0x04C].split(b'\x00')[0].decode('ascii', errors='replace')
    version_str = data[0x04C:0x05C].split(b'\x00')[0].decode('ascii', errors='replace')
    builder = data[0x064:0x07C].split(b'\x00')[0].decode('ascii', errors='replace')
    return {
        "magic": f"0x{magic:08X}",
        "version": f"{ver_major}.{ver_minor}.{ver_patch}.0",
        "version_string": version_str,
        "build_info": build_info,
        "builder": builder,
        "size": len(data),
    }


def generate_html_report(old_path: str, new_path: str, old_data: bytes, new_data: bytes,
                         byte_diffs: list, string_diff: dict, func_diff: dict,
                         old_header: dict, new_header: dict) -> str:
    """Generate HTML diff report."""
    h = html.escape

    # Categorize byte diffs by region
    regions = []
    for d in byte_diffs:
        if "type" in d and d["type"] == "size_change":
            continue
        off = d["offset"]
        if off < 0x200:
            region = "Header"
        elif off < 0xA0000:
            region = "Code"
        elif off < 0xC0000:
            region = "Strings/Data"
        elif off < 0x140000:
            region = "Tables/Mixed"
        else:
            region = "High Entropy"
        regions.append((region, d))

    region_counts = {}
    for region, _ in regions:
        region_counts[region] = region_counts.get(region, 0) + 1

    total_changed_bytes = sum(d.get("length", 0) for d in byte_diffs if "length" in d)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Firmware Diff: {h(old_header.get('version','?'))} vs {h(new_header.get('version','?'))}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }}
  h2 {{ color: #79c0ff; margin-top: 30px; }}
  h3 {{ color: #d2a8ff; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin: 10px 0; }}
  .stat {{ display: inline-block; margin: 10px 20px; text-align: center; }}
  .stat .value {{ font-size: 28px; font-weight: bold; color: #58a6ff; }}
  .stat .label {{ font-size: 12px; color: #8b949e; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th, td {{ border: 1px solid #30363d; padding: 6px 10px; text-align: left; }}
  th {{ background: #21262d; color: #79c0ff; }}
  .added {{ color: #3fb950; }}
  .removed {{ color: #f85149; }}
  .changed {{ color: #d29922; }}
  .hex {{ font-family: 'Fira Code', monospace; font-size: 12px; }}
  .diff-block {{ background: #1c2128; padding: 8px; border-radius: 4px; margin: 4px 0;
                 font-family: monospace; font-size: 11px; max-height: 200px; overflow-y: auto; }}
  .region-bar {{ display: inline-block; height: 20px; margin: 2px; border-radius: 3px; }}
  pre {{ background: #161b22; padding: 10px; border-radius: 4px; overflow-x: auto; }}
</style></head><body>
<h1>Whoop Firmware Diff Report</h1>
<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<div class="card">
  <h2>Summary</h2>
  <table>
    <tr><th></th><th>Old</th><th>New</th></tr>
    <tr><td>File</td><td>{h(old_path)}</td><td>{h(new_path)}</td></tr>
    <tr><td>Version</td><td>{h(old_header.get('version','?'))}</td><td>{h(new_header.get('version','?'))}</td></tr>
    <tr><td>Size</td><td>{old_header.get('size',0):,} bytes</td><td>{new_header.get('size',0):,} bytes</td></tr>
    <tr><td>Builder</td><td>{h(old_header.get('builder','?'))}</td><td>{h(new_header.get('builder','?'))}</td></tr>
    <tr><td>SHA256</td><td class="hex">{hashlib.sha256(old_data).hexdigest()[:16]}...</td>
        <td class="hex">{hashlib.sha256(new_data).hexdigest()[:16]}...</td></tr>
  </table>
</div>

<div class="card">
  <div class="stat"><div class="value">{len(byte_diffs)}</div><div class="label">Diff Regions</div></div>
  <div class="stat"><div class="value">{total_changed_bytes:,}</div><div class="label">Changed Bytes</div></div>
  <div class="stat"><div class="value changed">{func_diff['changed_count']}</div><div class="label">Changed Functions</div></div>
  <div class="stat"><div class="value added">{func_diff['added_count']}</div><div class="label">New Functions</div></div>
  <div class="stat"><div class="value removed">{func_diff['removed_count']}</div><div class="label">Removed Functions</div></div>
  <div class="stat"><div class="value added">{len(string_diff['added'])}</div><div class="label">New Strings</div></div>
  <div class="stat"><div class="value removed">{len(string_diff['removed'])}</div><div class="label">Removed Strings</div></div>
</div>

<div class="card">
  <h2>Byte Changes by Region</h2>
  <table>
    <tr><th>Region</th><th>Changes</th></tr>
    {''.join(f'<tr><td>{h(r)}</td><td>{c}</td></tr>' for r, c in sorted(region_counts.items()))}
  </table>
</div>

<div class="card">
  <h2>Byte-Level Diffs (first 50)</h2>
  <table>
    <tr><th>Offset</th><th>Length</th><th>Region</th><th>Old (hex)</th><th>New (hex)</th></tr>
    {''.join(
        f'<tr><td class="hex">0x{d["offset"]:06X}</td><td>{d.get("length",0)}</td>'
        f'<td>{regions[i][0] if i < len(regions) else "?"}</td>'
        f'<td class="hex removed">{" ".join(f"{b:02X}" for b in d.get("old",[][:16]))}'
        f'{"..." if d.get("length",0) > 16 else ""}</td>'
        f'<td class="hex added">{" ".join(f"{b:02X}" for b in d.get("new",[][:16]))}'
        f'{"..." if d.get("length",0) > 16 else ""}</td></tr>'
        for i, d in enumerate(byte_diffs[:50]) if "offset" in d
    )}
  </table>
</div>

<div class="card">
  <h2>String Changes</h2>
  <h3 class="added">Added Strings ({len(string_diff['added'])})</h3>
  <div class="diff-block">{'<br>'.join(h(s) for s in string_diff['added'][:50]) or '(none)'}</div>
  <h3 class="removed">Removed Strings ({len(string_diff['removed'])})</h3>
  <div class="diff-block">{'<br>'.join(h(s) for s in string_diff['removed'][:50]) or '(none)'}</div>
</div>

<div class="card">
  <h2>Function Changes</h2>
  <p>Old: {func_diff['old_total']} functions | New: {func_diff['new_total']} functions</p>
  <h3 class="changed">Modified Functions (first 30)</h3>
  <div class="diff-block">{'<br>'.join(f'0x{a:06X}' for a in func_diff['changed'][:30]) or '(none)'}</div>
  <h3 class="added">New Functions (first 30)</h3>
  <div class="diff-block">{'<br>'.join(f'0x{a:06X}' for a in func_diff['added'][:30]) or '(none)'}</div>
  <h3 class="removed">Removed Functions (first 30)</h3>
  <div class="diff-block">{'<br>'.join(f'0x{a:06X}' for a in func_diff['removed'][:30]) or '(none)'}</div>
</div>

</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Compare two Whoop firmware .bin files")
    parser.add_argument("old", help="Old firmware .bin file")
    parser.add_argument("new", help="New firmware .bin file")
    parser.add_argument("-o", "--output", default="firmware_diff_report.html",
                        help="Output HTML report path")
    args = parser.parse_args()

    old_path = Path(args.old)
    new_path = Path(args.new)

    if not old_path.exists():
        print(f"Error: {old_path} not found")
        sys.exit(1)
    if not new_path.exists():
        print(f"Error: {new_path} not found")
        sys.exit(1)

    print(f"Old: {old_path} ({old_path.stat().st_size:,} bytes)")
    print(f"New: {new_path} ({new_path.stat().st_size:,} bytes)")

    old_data = old_path.read_bytes()
    new_data = new_path.read_bytes()

    if old_data == new_data:
        print("\nFiles are IDENTICAL. No diff to generate.")
        return

    old_header = parse_header(old_data)
    new_header = parse_header(new_data)
    print(f"\nOld version: {old_header.get('version', '?')}")
    print(f"New version: {new_header.get('version', '?')}")

    print("\nAnalyzing byte differences...")
    byte_diffs = diff_bytes(old_data, new_data)
    total_changed = sum(d.get("length", 0) for d in byte_diffs if "length" in d)
    print(f"  {len(byte_diffs)} diff regions, {total_changed:,} bytes changed")

    print("Analyzing string differences...")
    string_diff = diff_strings(old_data, new_data)
    print(f"  +{len(string_diff['added'])} added, -{len(string_diff['removed'])} removed")

    print("Analyzing function differences...")
    func_diff = diff_functions(old_data, new_data)
    print(f"  +{func_diff['added_count']} added, -{func_diff['removed_count']} removed, "
          f"~{func_diff['changed_count']} changed")

    print(f"\nGenerating HTML report: {args.output}")
    report = generate_html_report(
        str(old_path), str(new_path), old_data, new_data,
        byte_diffs, string_diff, func_diff, old_header, new_header
    )
    Path(args.output).write_text(report)
    print(f"  Report saved ({Path(args.output).stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
