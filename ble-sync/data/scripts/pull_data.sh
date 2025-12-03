#!/bin/bash
# Pull Whoop sensor data from device via ADB (requires root)
# Usage: ./pull_data.sh
set -e

OUT_DIR="$(dirname "$0")/extracted_data"
mkdir -p "$OUT_DIR"

echo "=== Pulling Whoop cached_packet_db ==="
adb shell "su -c 'cp /data/data/com.whoop.android/databases/cached_packet_db* /sdcard/'"
adb pull /sdcard/cached_packet_db "$OUT_DIR/cached_packet_db"
adb pull /sdcard/cached_packet_db-shm "$OUT_DIR/cached_packet_db-shm" 2>/dev/null || true
adb pull /sdcard/cached_packet_db-wal "$OUT_DIR/cached_packet_db-wal" 2>/dev/null || true

echo "=== Pulling our app's database ==="
adb shell "su -c 'cp /data/data/com.whoopcapture/databases/whoop_capture.db* /sdcard/'"
adb pull /sdcard/whoop_capture.db "$OUT_DIR/whoop_capture.db" 2>/dev/null || true
adb pull /sdcard/whoop_capture.db-shm "$OUT_DIR/whoop_capture.db-shm" 2>/dev/null || true
adb pull /sdcard/whoop_capture.db-wal "$OUT_DIR/whoop_capture.db-wal" 2>/dev/null || true

echo "=== Decoding to CSV ==="
python3 "$(dirname "$0")/decode_to_csv.py" "$OUT_DIR"

echo "=== Done ==="
