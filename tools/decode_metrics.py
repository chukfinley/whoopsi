#!/usr/bin/env python3
"""Decode Whoop metrics binary data from HAR files.

Each HAR contains POST to /metrics-service/v1/metrics with binary payload.
Payload = concatenated 124-byte records, each structured as:
  [0-2]   aa 01 74 = protobuf framing (field 21, 116 bytes)
  [3-118]  116-byte payload
  [119-123] trailing bytes (likely checksum/footer)

Inner payload layout (116 bytes, offsets relative to payload start = record+3):
  ...to be determined...
"""

import json
import sys
import base64
import struct
import csv
from datetime import datetime, timezone
from pathlib import Path


RECORD_SIZE = 124
HEADER_SIZE = 3  # aa 01 74


def decode_har(filepath):
    with open(filepath) as f:
        har = json.load(f)
    entry = har["log"]["entries"][0]
    req = entry["request"]
    headers = {h["name"]: h["value"] for h in req["headers"]}
    meta = {
        "url": req["url"],
        "time": entry["startedDateTime"],
        "strap": headers.get("x-whoop-strap-id"),
        "hw": headers.get("x-whoop-hw-version"),
        "fw": headers.get("x-whoop-fw-version"),
        "bin_ver": headers.get("x-whoop-binary-version"),
        "response": entry["response"]["content"].get("text", ""),
    }
    return base64.b64decode(req["_content"]["text"]), meta


def parse_records(data):
    """Split data into 124-byte records and extract fields."""
    n = len(data) // RECORD_SIZE
    records = []
    for i in range(n):
        r = data[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
        p = r[HEADER_SIZE : HEADER_SIZE + 116]  # payload

        # Timestamp at payload offset 12 (LE uint32, Unix seconds)
        ts_unix = struct.unpack("<I", p[12:16])[0]
        ts = datetime.fromtimestamp(ts_unix, timezone.utc) if ts_unix > 1_000_000_000 else None

        # Sequence at payload offset 11 (or bytes 14-15 of record as BE uint16 = 0077, 0078...)
        seq_be = struct.unpack(">H", r[14:16])[0]

        # Byte 22 of record = payload offset 19: possible heart rate
        hr_candidate = p[19]

        # Payload offset 21-22 (LE uint16)
        val_21 = struct.unpack("<H", p[21:23])[0]

        # Look at floats - try various offsets within payload
        # Payload bytes 37-52 had interesting float-like patterns
        floats = {}
        for off in range(28, 60, 4):
            try:
                f_be = struct.unpack(">f", p[off : off + 4])[0]
                f_le = struct.unpack("<f", p[off : off + 4])[0]
                floats[f"be_{off}"] = f_be
                floats[f"le_{off}"] = f_le
            except:
                pass

        # Payload offset 7-10: incrementing value (byte 8 increments)
        incr_val = struct.unpack(">I", p[7:11])[0]

        # Trailing 5 bytes of record
        trail = r[119:124]
        trail_u32 = struct.unpack("<I", trail[0:4])[0]

        records.append({
            "idx": i,
            "ts_unix": ts_unix,
            "ts": ts,
            "seq": seq_be,
            "incr": incr_val,
            "hr": hr_candidate,
            "val21": val_21,
            "floats": floats,
            "trail": trail,
            "payload": p,
        })

    return records


def analyze_payload_structure(records):
    """Compare all payloads to understand field semantics."""
    if not records:
        return

    # Check timestamp intervals
    ts_diffs = [records[i + 1]["ts_unix"] - records[i]["ts_unix"]
                for i in range(len(records) - 1)
                if records[i]["ts_unix"] > 0 and records[i + 1]["ts_unix"] > 0]

    if ts_diffs:
        print(f"Timestamp intervals: min={min(ts_diffs)}s max={max(ts_diffs)}s "
              f"avg={sum(ts_diffs)/len(ts_diffs):.1f}s")
        unique_diffs = sorted(set(ts_diffs))
        if len(unique_diffs) <= 10:
            print(f"  Unique intervals: {unique_diffs}")

    # HR-like field range
    hrs = [r["hr"] for r in records]
    print(f"HR candidate (byte 22): min={min(hrs)} max={max(hrs)} avg={sum(hrs)/len(hrs):.1f}")

    # val21 range
    v21s = [r["val21"] for r in records]
    print(f"Val21 (payload[21:23] LE): min={min(v21s)} max={max(v21s)} avg={sum(v21s)/len(v21s):.1f}")

    # Check which float offsets have reasonable accel/gyro ranges
    print("\nFloat field ranges:")
    for off in range(28, 60, 4):
        be_vals = [r["floats"][f"be_{off}"] for r in records if f"be_{off}" in r["floats"]]
        le_vals = [r["floats"][f"le_{off}"] for r in records if f"le_{off}" in r["floats"]]
        for label, vals in [("BE", be_vals), ("LE", le_vals)]:
            if vals:
                mn, mx = min(vals), max(vals)
                if -100 < mn and mx < 100:
                    print(f"  payload[{off}:{off+4}] {label}: {mn:.4f} .. {mx:.4f} (avg={sum(vals)/len(vals):.4f}) ***")
                elif -10000 < mn and mx < 10000:
                    print(f"  payload[{off}:{off+4}] {label}: {mn:.2f} .. {mx:.2f}")


def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else ["1POST api.prod.whoop.com.har"]

    for filepath in files:
        print(f"{'='*60}")
        print(f"File: {filepath}")
        print(f"{'='*60}\n")

        data, meta = decode_har(filepath)
        for k, v in meta.items():
            print(f"  {k}: {v}")
        print(f"  raw_size: {len(data)} bytes")
        print()

        records = parse_records(data)
        print(f"Records: {len(records)}\n")

        if not records:
            continue

        analyze_payload_structure(records)

        # Print first 10 records
        print(f"\n{'#':>3} {'Timestamp':>22} {'Seq':>5} {'HR':>3} {'Val21':>6}")
        for r in records[:20]:
            ts_str = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else "N/A"
            print(f"{r['idx']:3d} {ts_str:>22} {r['seq']:5d} {r['hr']:3d} {r['val21']:6d}")

        if len(records) > 20:
            print(f"  ... ({len(records) - 20} more records)")
            r = records[-1]
            ts_str = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else "N/A"
            print(f"{r['idx']:3d} {ts_str:>22} {r['seq']:5d} {r['hr']:3d} {r['val21']:6d}")

        print()

        # Deep analysis: scan every byte position for potential data types
        print("=== Per-byte-position type analysis ===\n")
        payloads = [r["payload"] for r in records]
        for off in range(0, 116):
            vals = [p[off] for p in payloads]
            unique = len(set(vals))
            if unique == 1:
                continue  # skip constant bytes

            mn, mx = min(vals), max(vals)
            spread = mx - mn

            # Check if incrementing
            diffs = [vals[i+1] - vals[i] for i in range(len(vals)-1)]
            is_monotonic = all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)

            note = ""
            if is_monotonic and spread > 5:
                note = " MONOTONIC"
            if 40 <= mn <= 70 and mx <= 220 and spread < 60:
                note += " HR?"
            if unique <= 3:
                note += f" LOW_VAR({set(vals)})"

            if note or spread > 3:
                print(f"  byte[{off:3d}]: range={mn:3d}-{mx:3d} unique={unique:3d}{note}")

        # Try 16-bit LE at various offsets
        print("\n=== 16-bit LE field scan ===\n")
        for off in range(0, 115):
            vals = [struct.unpack("<H", p[off:off+2])[0] for p in payloads]
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            if 30 < avg < 300 and mx < 500:
                print(f"  u16le[{off:3d}]: range={mn}-{mx} avg={avg:.1f} (physio?)")
            elif 500 < avg < 20000 and mn > 100:
                print(f"  u16le[{off:3d}]: range={mn}-{mx} avg={avg:.1f}")

        # Try 32-bit float BE at various 4-byte aligned offsets
        print("\n=== 32-bit float BE scan ===\n")
        for off in range(0, 113):
            vals = [struct.unpack(">f", p[off:off+4])[0] for p in payloads]
            # Filter out inf/nan
            vals = [v for v in vals if v == v and abs(v) < 1e10]
            if len(vals) < len(payloads) * 0.8:
                continue
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            if -10 < mn and mx < 10 and (mx - mn) > 0.01:
                print(f"  f32be[{off:3d}]: range={mn:.4f}..{mx:.4f} avg={avg:.4f} ***")
            elif -1000 < mn and mx < 1000 and (mx - mn) > 1:
                print(f"  f32be[{off:3d}]: range={mn:.2f}..{mx:.2f} avg={avg:.2f}")

        # Same for LE
        print("\n=== 32-bit float LE scan ===\n")
        for off in range(0, 113):
            vals = [struct.unpack("<f", p[off:off+4])[0] for p in payloads]
            vals = [v for v in vals if v == v and abs(v) < 1e10]
            if len(vals) < len(payloads) * 0.8:
                continue
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            if -10 < mn and mx < 10 and (mx - mn) > 0.01:
                print(f"  f32le[{off:3d}]: range={mn:.4f}..{mx:.4f} avg={avg:.4f} ***")
            elif -1000 < mn and mx < 1000 and (mx - mn) > 1:
                print(f"  f32le[{off:3d}]: range={mn:.2f}..{mx:.2f} avg={avg:.2f}")


if __name__ == "__main__":
    main()
