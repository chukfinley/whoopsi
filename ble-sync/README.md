# Whoop Companion (BLE Sensor Capture)

Android app that connects to Whoop 4.0/5.0 straps via Bluetooth Low Energy and downloads raw sensor data directly from the strap's circular flash buffer.

## What It Does

The Whoop strap continuously records sensor data at ~1 sample/second and stores it in a circular flash buffer (~20 days capacity). This app connects via BLE, sends the right AA01-framed commands, and downloads all that raw data into a local SQLite database.

**Data captured per sample:**
- Heart rate (computed from RR intervals, ms precision)
- SpO2 (raw value, add 10 for percentage)
- 3-axis accelerometer (g-force, IEEE 754 float)
- Gyroscope
- Skin temperature (via separate sensor)
- Unix timestamp (1-second resolution)

## Build & Install

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Requires Android SDK 26+ and Bluetooth permissions.

## Usage

### Sync Modes

**Smart Sync** ("Sync Now" button):
- Starts from current trim position, downloads incrementally
- Stops after 15 consecutive all-duplicate bursts (data already in DB)
- Fast for daily syncs (~1-5 minutes)

**Full Sync** (orange button):
- Disconnects and reconnects BLE to reset the trim pointer
- Downloads the entire circular buffer from the beginning
- Takes 8-10 hours for a full 20-day buffer (~1.3M records)

**Auto Sync:**
- WorkManager runs every 6 hours in the background
- Same logic as Smart Sync

### Pull Data to Computer

```bash
adb shell "run-as com.whoopcapture cat databases/whoop_capture.db" > whoop_capture.db
```

The database has a single `sensor_records` table with columns: `id`, `timestamp`, `hr`, `spo2`, `accel_x/y/z`, `gyro`, `rr1_ms`, `rr2_ms`, `raw_hex`.

## How It Works

### BLE Protocol (AA01)

All communication uses a proprietary framing protocol:

```
[0xAA][0x01][length:2LE][routing:2][CRC16:2LE][type:1][payload:var][CRC32:4LE]
```

The app sends commands on `fd4b0002` (CMD_TO_STRAP) and receives responses on `fd4b0003` (CMD_FROM_STRAP), events on `fd4b0004`, and sensor data on `fd4b0005`.

### Sync Flow (matches official Whoop app)

1. `GET_DATA_RANGE` (0x22) &mdash; check what data is available
2. `SEND_HISTORICAL_DATA` (0x16) &mdash; trigger dump (sent once)
3. Receive burst of ~50 data packets (0x2F) on fd4b0005
4. Receive `HISTORY_END` event (0x31) with sector:offset
5. Send `HISTORICAL_DATA_RESULT` (0x17) ACK with those bytes
6. Strap automatically sends next burst
7. Repeat 3-6 until `HISTORY_COMPLETE` event

### Sensor Packet Format (0x2F, 124 bytes)

```
Offset  Field
7-10    Timestamp (Unix seconds, uint32 LE)
14      SpO2 raw (add 10 for %)
15      RR interval count (0, 1, or 2)
16-17   RR interval 1 (ms, uint16 LE) -> HR = 60000/RR1
18-19   RR interval 2 (ms, uint16 LE)
36-39   Gyroscope (float32 BE)
40-43   Accelerometer X (float32 BE)
44-47   Accelerometer Y (float32 BE)
48-51   Accelerometer Z (float32 BE)
```

~45% of packets have valid RR data (strap worn with heartbeat detected).

## Architecture

```
MainActivity.kt          Jetpack Compose UI, sync controls, stats
WhoopBleService.kt       BLE connection, sync loop, notification handling
WhoopProtocol.kt         AA01 packet builder (CRC16/CRC32, 4-byte alignment)
WhoopDataDecoder.kt      Parses 0x2F sensor packets into structured data
WhoopUuids.kt            BLE service/characteristic UUIDs
ChartScreen.kt           HR/SpO2 time-series charts
AutoSyncWorker.kt        WorkManager periodic sync
db/                      Room database (SensorRecord entity + DAO)
```

## Coexistence with Official App

The companion app can run alongside the official Whoop app. Only one app can hold an active GATT connection at a time &mdash; the companion connects on demand and disconnects when done.

## Key Discovery: Per-Bond Trim State

The strap maintains separate trim pointers per bonded device. Each paired phone gets its own "bookmark" of where it left off. This means:
- The companion app's sync doesn't affect the official app's sync
- Full Sync works by disconnecting/reconnecting (fresh BLE session)
- `FORCE_TRIM_ALL` permanently marks data consumed for that bond (never use this)

## Full Protocol Documentation

See [CLAUDE.md](CLAUDE.md) for the complete reverse-engineered BLE protocol documentation (40+ commands, packet formats, CRC algorithms, authentication flow).
