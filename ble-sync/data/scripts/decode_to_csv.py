#!/usr/bin/env python3
"""Decode Whoop sensor packets from SQLite databases to CSV."""
import sqlite3
import struct
import csv
import sys
import os
from datetime import datetime

def decode_packet(data):
    """Decode a 124-byte Whoop sensor packet."""
    if len(data) < 124:
        return None
    p = 3  # skip AA 01 74 protobuf framing
    try:
        unix_ts = struct.unpack_from('<I', data, p + 12)[0]
        return {
            'unix_ts': unix_ts,
            'datetime': datetime.fromtimestamp(unix_ts).strftime('%Y-%m-%d %H:%M:%S'),
            'heart_rate': data[p + 19],
            'rr_count': data[p + 20],
            'rr1_ms': struct.unpack_from('<H', data, p + 21)[0],
            'rr2_ms': struct.unpack_from('<H', data, p + 23)[0],
            'rr3_ms': struct.unpack_from('<H', data, p + 25)[0],
            'gyro': round(struct.unpack_from('>f', data, p + 29)[0], 4),
            'accel_x': round(struct.unpack_from('>f', data, p + 45)[0], 4),
            'accel_y': round(struct.unpack_from('>f', data, p + 49)[0], 4),
            'accel_z': round(struct.unpack_from('>f', data, p + 53)[0], 4),
            'spo2_raw': data[p + 55],
            'spo2_percent': data[p + 55] + 10,
            'byte16': data[p + 16],
            'byte17': data[p + 17],
            'byte66': data[p + 66],
            'byte68': data[p + 68],
            'byte105': data[p + 105],
            'byte106': data[p + 106],
            'raw_hex': data.hex(),
        }
    except Exception as e:
        return None

def process_whoop_db(db_path, all_records):
    """Read cached_packet_db from Whoop app."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT timestamp, packet_data FROM cached_table_metrics_data ORDER BY timestamp'
        ).fetchall()
        for ts_ms, data in rows:
            rec = decode_packet(data)
            if rec:
                rec['source'] = 'whoop_cache'
                rec['db_timestamp_ms'] = ts_ms
                all_records[rec['unix_ts']] = rec
    except Exception as e:
        print(f"  Error reading {db_path}: {e}")
    conn.close()

def process_capture_db(db_path, all_records):
    """Read our app's database."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT timestamp, heartRate, rrCount, rr1Ms, rr2Ms, rr3Ms, '
            'accelX, accelY, accelZ, gyro, spo2Percent, '
            'byte16, byte17, byte66, byte68, byte105, byte106, rawHex '
            'FROM sensor_records ORDER BY timestamp'
        ).fetchall()
        for row in rows:
            ts = row[0]
            if ts not in all_records:
                all_records[ts] = {
                    'unix_ts': ts,
                    'datetime': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
                    'heart_rate': row[1], 'rr_count': row[2],
                    'rr1_ms': row[3], 'rr2_ms': row[4], 'rr3_ms': row[5],
                    'accel_x': row[6], 'accel_y': row[7], 'accel_z': row[8],
                    'gyro': row[9], 'spo2_percent': row[10], 'spo2_raw': row[10] - 10,
                    'byte16': row[11], 'byte17': row[12],
                    'byte66': row[13], 'byte68': row[14],
                    'byte105': row[15], 'byte106': row[16],
                    'raw_hex': row[17],
                    'source': 'capture_app',
                    'db_timestamp_ms': 0,
                }
    except Exception as e:
        print(f"  Error reading {db_path}: {e}")
    conn.close()

def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else 'extracted_data'
    all_records = {}  # keyed by unix_ts for dedup

    print("Reading Whoop cached_packet_db...")
    process_whoop_db(os.path.join(data_dir, 'cached_packet_db'), all_records)
    print(f"  {len(all_records)} records")

    print("Reading capture app DB...")
    process_capture_db(os.path.join(data_dir, 'whoop_capture.db'), all_records)
    print(f"  {len(all_records)} total records (after dedup)")

    if not all_records:
        print("No records found!")
        return

    # Sort by timestamp
    sorted_records = sorted(all_records.values(), key=lambda r: r['unix_ts'])

    # Write CSV
    csv_path = os.path.join(data_dir, 'whoop_all_data.csv')
    fields = ['unix_ts', 'datetime', 'heart_rate', 'rr_count', 'rr1_ms', 'rr2_ms', 'rr3_ms',
              'accel_x', 'accel_y', 'accel_z', 'gyro', 'spo2_percent', 'spo2_raw',
              'byte16', 'byte17', 'byte66', 'byte68', 'byte105', 'byte106',
              'source', 'db_timestamp_ms']
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted_records)

    # Stats
    hrs = [r['heart_rate'] for r in sorted_records if r['heart_rate'] > 0]
    print(f"\nExported {len(sorted_records)} records to {csv_path}")
    print(f"Time range: {sorted_records[0]['datetime']} -> {sorted_records[-1]['datetime']}")
    if hrs:
        print(f"HR: min={min(hrs)}, max={max(hrs)}, avg={sum(hrs)/len(hrs):.0f} BPM")

if __name__ == '__main__':
    main()
