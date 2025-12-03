# Whoop BLE Protocol — Reverse Engineering Documentation

Complete documentation of the Whoop 4.0/5.0 (Maverick) BLE protocol, reverse-engineered from the official Whoop Android APK (decompiled) and HCI snoop log captures.

## BLE Service UUIDs

### Maverick / Whoop 5.0
| Characteristic | UUID |
|---|---|
| Service | `fd4b0001-cce1-4033-93ce-002d5875f58a` |
| CMD_TO_STRAP | `fd4b0002-cce1-4033-93ce-002d5875f58a` |
| CMD_FROM_STRAP | `fd4b0003-cce1-4033-93ce-002d5875f58a` |
| EVENTS_FROM_STRAP | `fd4b0004-cce1-4033-93ce-002d5875f58a` |
| DATA_FROM_STRAP | `fd4b0005-cce1-4033-93ce-002d5875f58a` |
| MEMFAULT | `fd4b0007-cce1-4033-93ce-002d5875f58a` |

### Gen 4 (Harvard)
Same pattern with prefix `61080001` through `61080007`, base `8d6d-82b8-614a-1c8cb0f8dcc6`.

### Puffin
Same pattern with prefix `11500001` through `11500007`, base `6215-11ee-8c99-0242ac120002`.

Source: `Po/p.java`, `Po/o.java` in decompiled APK.

---

## AA01 Packet Format

All BLE data uses this framing. Every write to CMD_TO_STRAP and every notification from CMD_FROM_STRAP, EVENTS, and DATA uses this format.

```
Offset  Size  Field           Description
------  ----  -----           -----------
0       1     SOF             Start of Frame = 0xAA
1       1     Revision        0x01 for Maverick
2-3     2     Length (LE)     Bytes from offset 8 to end of packet (includes CRC32)
4-5     2     Routing         0x00 0x01 = App→Strap, 0x01 0x00 = Strap→App
6-7     2     Header CRC16    CRC-16/MODBUS on bytes 0-5, stored little-endian
--- header ends (8 bytes), payload begins ---
8       1     Cmd Type        0x23 = COMMAND (to strap), 0x24 = RESPONSE (from strap)
9       1     Sequence        Incrementing counter (0x00-0xFF)
10      1     Cmd Code        Identifies the command
11+     N     Parameters      Command-specific data
last 4  4     CRC32 (LE)      Standard CRC32 on bytes 8..end-4
```

### CRC Algorithms

**Header CRC-16/MODBUS:**
- Polynomial: 0xA001 (reflected form of 0x8005)
- Initial value: 0xFFFF
- Input: bytes 0-5
- Stored little-endian at bytes 6-7

```python
def crc16_modbus(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc
```

**Payload CRC32:**
- Standard CRC32 (same as zlib, Java `CRC32`, Python `binascii.crc32`)
- Init: 0xFFFFFFFF, final XOR: 0xFFFFFFFF
- Input: bytes 8 to (packet_size - 4)
- Stored little-endian as last 4 bytes

Source: `Qo/C9986c.java`, `Jo/C8769e.java` in decompiled APK. Verified against all 82 command packets in HCI capture.

---

## Command Codes

Sent in byte 10 of the AA01 payload. Byte 8 is 0x23 (COMMAND) for outgoing, 0x24 (RESPONSE) for incoming.

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| 0x03 | TOGGLE_REALTIME_HR | TX→RX | Enable/disable real-time HR streaming |
| 0x07 | REPORT_VERSION_INFO | TX→RX | Get firmware version info |
| 0x0A | SET_CLOCK | TX→RX | Set strap time |
| 0x0B | GET_CLOCK | TX→RX | Get strap time |
| 0x0E | ALARM_CONFIG | TX→RX | Configure alarm settings |
| 0x10 | TOGGLE_R7_DATA | TX→RX | Toggle R7 sensor data |
| 0x13 | CONFIGURE_SENSOR | TX→RX | Configure sensor parameters |
| 0x14 | ABORT_HISTORICAL | TX→RX | Abort ongoing historical data transfer |
| 0x16 | SEND_HISTORICAL_DATA | TX→RX | Trigger strap to dump all un-trimmed history |
| 0x17 | HISTORICAL_DATA_RESULT | RX→TX | Ack sent by app after receiving data burst |
| 0x19 | FORCE_TRIM | TX→RX | Set trim pointer: (0,0)=rewind, (0xFEFEFEFE,0xFEFEFEFE)=trim all |
| 0x1A | GET_BATTERY_LEVEL | TX→RX | Query battery percentage |
| 0x21 | SET_READ_POINTER | TX→RX | Set read pointer to sector:offset |
| 0x22 | GET_DATA_RANGE | TX→RX | Query available data range, returns timestamps |
| 0x42 | SET_ALARM_TIME | TX→RX | Configure alarm time |
| 0x8D | GET_ADVERTISING_NAME | TX→RX | Returns "Whoop" + hardware info |
| 0x91 | GET_HELLO_EXT | TX→RX | Returns serial number (e.g. <STRAP_SERIAL>) + hash |

### Command Parameter Formats

**IMPORTANT:** All command payloads MUST be 4-byte aligned (padded with 0x00). The strap firmware rejects unaligned payloads with "error = 4".

**GET_HELLO_EXT (0x91):** `params = [0x01]`
- Response: ASCII serial number + device hash

**GET_ADVERTISING_NAME (0x8D):** `params = [0x01]`
- Response: `[hw_rev, fw1, fw2, name_bytes...]` where name is null-terminated ASCII

**GET_DATA_RANGE (0x22):** `params = []` (empty, padded to 4 bytes → `[0x00]`)
- Response: 69+ bytes containing multiple LE uint32 Unix timestamps
- Timestamps at byte offsets 35, 43, 51, 59 in response params (NOT 4-byte aligned!)
- Offset 35: earliest data timestamp, offset 59: latest/current timestamp
- Also contains sector:offset info for flash layout

**SEND_HISTORICAL_DATA (0x16):** `params = []` (empty, padded → `[0x00]`)
- Triggers strap to dump all data from current trim position to write head
- Data arrives as 0x2F packets on DATA_FROM_STRAP (fd4b0005)
- Strap sends in bursts of ~50 packets, each advancing the internal read position
- After each burst: 0x31 HISTORY_END event on fd4b0005 with sector:offset
- App must send HISTORICAL_DATA_RESULT ACK (0x17) with the event's sector:offset
- Strap automatically continues with next burst (no need to re-send 0x16!)
- After all data: 0x31 HISTORY_COMPLETE event (metadata type 3)
- Console log "Historical Dump Complete" + PullStats (informational only, can be stale)

**HISTORICAL_DATA_RESULT (0x17):** `params = [status(1), sector(4), offset(4)]` = 9 bytes
- status: 0x01 = SUCCESS
- sector + offset: raw bytes from the 0x31 HISTORY_END event (F() and H() in decompiled APK)
- Official app copies raw bytes directly, not interpreted integers
- Sent after each burst to acknowledge receipt; strap continues with next burst

**FORCE_TRIM (0x19):** `params = [sector(4 LE), offset(4 LE)]`
- `(0, 0)` = attempts to rewind trim pointer (WARNING: only exposes wrap-around segment, not full buffer)
- `(0xFEFEFEFE, 0xFEFEFEFE)` = "Trim All" sentinel → **PERMANENTLY** marks ALL data consumed for this bond identity (NEVER use during sync!)
- `(sector, offset)` = set trim to specific position
- Strap console: "entering Trim All mode" / "TrimAllCount=N, leaving TrimAll mode"
- **Official Whoop app NEVER uses FORCE_TRIM during sync** — it's a debug/maintenance command

**SET_READ_POINTER (0x21):** `params = [sector(4 LE), offset(4 LE)]`
- Sets the read pointer for flash data access
- `(0, 0)` = rewind to beginning
- Not used by official app (defined but unused in decompiled APK)

**Historical Data Sync Flow (matches official app):**
1. `GET_DATA_RANGE` (0x22) — check available data range
2. `SEND_HISTORICAL_DATA` (0x16) — trigger dump (sent ONCE)
3. Receive burst of ~50 data packets (0x2F) on fd4b0005
4. Receive 0x31 HISTORY_END event (metadata type 2) with sector:offset
5. Send `HISTORICAL_DATA_RESULT` (0x17) ACK with those sector:offset bytes
6. Strap automatically sends next burst (go to step 3)
7. Repeat until 0x31 HISTORY_COMPLETE event (metadata type 3)
- **Do NOT re-send SEND_HISTORICAL_DATA for each burst** — the ACK triggers the next burst
- **Do NOT use FORCE_TRIM before sync** — just send SEND_HISTORICAL_DATA on a clean connection

**0x31 Event Packet Format (HISTORY_START/END/COMPLETE):**
- Arrives on fd4b0005 as AA01-framed packet
- inner[0] = 0x31 (packet type)
- inner[1] = sequence counter
- inner[2] = metadata type: 1=HISTORY_START, 2=HISTORY_END, 3=HISTORY_COMPLETE
- inner[3..6] = timestamp (uint32 LE)
- For HISTORY_END (24 bytes):
  - inner[13..16] = sector bytes (raw, copy directly into ACK)
  - inner[17..20] = offset bytes (raw, copy directly into ACK)
  - These correspond to F() and H() in the decompiled official app's C10257b class
  - The official app uses D() to slice from parent position 9, then reads at [4..7] and [8..11]

**Flash Layout:**
- Circular buffer in sector 10 (0x0000000a)
- Offsets wrap around ~131068 (0x0001fffc)
- **Observed capacity: ~20 days** (confirmed via full sync)
- Trim pointer is **per bond identity** — NOT shared between devices
- FORCE_TRIM_ALL permanently marks data consumed for the current bond
- To reset trim: unpair and re-pair the device (new bond = fresh trim)

**Critical Discovery: Per-Bond Trim State**
The strap maintains separate trim pointers per bonded device. Our FORCE_TRIM_ALL calls
permanently marked all data consumed for our phone's bond identity. Connecting the same
strap to a DIFFERENT phone pulled all data successfully. The fix: unpair in Android
Bluetooth settings → re-pair → fresh bond identity → full buffer available.

Source: Decompiled APK (`Io/e.java`, `Nl/C9465g.java`), BLE capture analysis.

---

## Official App Init Sequence

Captured via HCI snoop log. The official Whoop app sends these commands immediately after connecting:

1. `GET_HELLO_EXT` (0x91, subcmd=0x01) — serial + device info
2. `GET_ADVERTISING_NAME` (0x8D, subcmd=0x01) → "Whoop"
3. `GET_DATA_RANGE` (0x22) → available data timestamps
4. `SEND_HISTORICAL_DATA` (0x16) → triggers data dump
5. `HISTORICAL_DATA_RESULT` (0x17) — ack after each burst
6. `FORCE_TRIM` (0x19, with final trim position) → advance trim pointer
7. Repeat 4-6 until all data synced

After command 4, the strap floods DATA_FROM_STRAP with hundreds of notifications containing sensor data (0x2F), events (0x31), and console logs (0x32).

## Our Companion App Init Sequence

### Smart Sync (default — on auto-connect and "Sync Now")
1. Subscribe to fd4b0003 (CMD_FROM), fd4b0004 (EVENTS), fd4b0005 (DATA)
2. `ABORT_HISTORICAL` (0x14) — stop any leftover dump from previous connection
3. `GET_HELLO_EXT` (0x91) + `GET_BATTERY_LEVEL` (0x1A) + `GET_EXTENDED_BATTERY_INFO` (0x62)
4. `GET_DATA_RANGE` (0x22) — check strap's data range
5. `SEND_HISTORICAL_DATA` (0x16) — start dump (sent ONCE)
6. Event-driven loop:
   a. Receive burst of ~50 data packets (0x2F)
   b. Receive 0x31 HISTORY_END event with sector:offset
   c. Send HISTORICAL_DATA_RESULT (0x17) ACK with those sector:offset bytes
   d. Strap automatically sends next burst
7. Stop after 15 consecutive all-duplicate bursts (data already in DB)
8. Stop when caught up to present (latest timestamp within 5 min of now)
- No FORCE_TRIM used — matches official app behavior
- Smart Sync stops early on duplicates; Full Sync does not

### Full Sync (explicit — "Full Sync" button)
1. Stop any running sync
2. Disconnect BLE completely
3. Wait 3 seconds for BLE stack cleanup
4. Reconnect — fresh connection resets the strap's trim for this bond
5. `GET_DATA_RANGE` (0x22) — verify full buffer available
6. `SEND_HISTORICAL_DATA` (0x16) — start dump (sent ONCE)
7. Event-driven loop (same as Smart Sync steps 6a-6d)
8. Runs until HISTORY_COMPLETE event (no duplicate limit)
9. maxRounds = 50000 to handle full 20-day buffer
- Reconnect is needed because FORCE_TRIM(0,0) only exposes the wrap-around segment
- Full buffer download: ~8-10 hours at ~50 pkts/sec for 20 days of data
- Duplicates handled via UNIQUE constraint — safe to re-download

### Trim Pointer Behavior
- Trim is **per bond identity** — each paired device has its own trim state on the strap
- On fresh BLE connect (same bond): trim resumes from where last session left off
- On fresh bond (re-pair): trim starts at oldest data → full buffer available
- `GET_DATA_RANGE` @43 = data start timestamp (trim position for this bond)
- `GET_DATA_RANGE` @59 = current write head timestamp (strap's current time)
- FORCE_TRIM_ALL (0xFEFEFEFE) permanently marks ALL data consumed for this bond
- **NEVER use FORCE_TRIM_ALL** — it cannot be undone without re-pairing
- The official Whoop app does NOT use FORCE_TRIM during sync at all
- To recover from FORCE_TRIM_ALL: unpair in Android BT settings → re-pair
- Connecting same strap to a different phone pulls all data (confirmed)

### Bugs Fixed
1. **dumpComplete from stale console logs**: Console log "Historical Dump Complete" fragments
   from previous dumps would set dumpComplete=true, aborting sync after ~28 packets.
   Fix: Only set dumpComplete from 0x31 HISTORY_COMPLETE event (metadata type 3).
2. **FORCE_TRIM_ALL in Full Sync flow**: Permanently consumed all data for this bond.
   Fix: Removed — use disconnect/reconnect instead.
3. **Re-sending SEND_HISTORICAL_DATA per burst**: Caused overlapping bursts and packet counting errors.
   Fix: Send SEND_HISTORICAL_DATA only ONCE, then ACK-driven loop.
4. **ACK byte format**: Used 12-byte buffer instead of 9-byte (matching official app).
   Fix: Allocate exactly 9 bytes: [status:1, sector:4, offset:4].
5. **burstComplete breaking wait too early**: 0x31 event arrived before data packets finished.
   Fix: Wait for data stall (2s silence) after burstComplete, not just burstComplete alone.

---

## Data Packet Types

All arrive as AA01-framed notifications on DATA_FROM_STRAP (fd4b0005).

The inner payload (bytes 8 to end-4 of the AA01 frame) starts with a packet type byte:

| Type | Name | Typical Size | Description |
|------|------|-------------|-------------|
| 0x2F | HISTORICAL_DATA | 124 bytes total | Sensor records (HR, accel, SpO2, etc.) |
| 0x31 | EVENTS | 24-40 bytes | Event notifications (wear detect, etc.) |
| 0x32 | CONSOLE_LOGS | 64 bytes | ASCII debug messages from firmware |

### Type 0x2F — Historical Sensor Data (124 bytes in AA01 frame)

The inner payload after AA01 extraction contains the sensor record. The full AA01 packet is 124 bytes, inner payload is 112 bytes (124 - 8 header - 4 CRC32).

### Type 0x32 — Console Logs

ASCII text from the strap firmware. Contains debug messages like:
- `SIGPROC-WEAR-DETECT V5: had an opt/amb OFF transition`
- `BLE: History burst success. Trim: 0x0000000a:00050600`
- `SENSORS: No active optical data collection sources`

Useful for debugging but not stored in the sensor database.

---

## AA01 0x2F Sensor Packet Format

These records arrive as AA01-framed notifications on DATA_FROM_STRAP (fd4b0005).
There are TWO sub-formats depending on total AA01 packet size:

### Format A: 124-byte AA01 (112-byte inner) — Historical Data

This is the primary format for historical dumps (~99.999% of packets).

**Heart rate is NOT stored directly.** Instead, RR intervals (inter-beat intervals in ms) are at inner[15:21]. Compute HR = 60000 / RR1_ms.

```
Offset  Size  Type     Field
------  ----  ----     -----
0       1     byte     Packet type = 0x2F
1-2     2     bytes    Sequence / routing (inner[1]=0x12 for this format)
3-6     4     bytes    Sub-header (revision, counter)
7-10    4     uint32   Timestamp (Unix seconds, LE) ← VERIFIED
11-13   3     bytes    Flags / sample info
14      1     uint8    SpO2 raw (add 10 for percentage when 1-99) ← VERIFIED
15      1     uint8    RR Interval Count (0, 1, or 2) ← VERIFIED
16-17   2     uint16   RR Interval 1 (ms, LE) ← VERIFIED (HR = 60000/RR1)
18-19   2     uint16   RR Interval 2 (ms, LE) ← VERIFIED
20-24   5     bytes    Zeros / reserved
25-35   11    bytes    Signal processing / optical data
36-39   4     float32  Gyroscope (BE IEEE 754) ← VERIFIED
40-43   4     float32  Accelerometer X (g, BE) ← VERIFIED
44-47   4     float32  Accelerometer Y (g, BE) ← VERIFIED
48-51   4     float32  Accelerometer Z (g, BE) ← VERIFIED
52+     60    bytes    Additional sensor data, config bytes
```

~45% of packets have valid RR data (band worn with heartbeat detected).
Typical RR values: 500-1200ms (50-120 BPM).

### Format B: 88-byte AA01 (76-byte inner) — Real-time / Compact

Rare format with direct HR BPM value.

```
Offset  Size  Type     Field
------  ----  ----     -----
0       1     byte     Packet type = 0x2F
1-2     2     bytes    Sequence / routing (inner[1]=0x1a for this format)
3-6     4     bytes    Sub-header
7-10    4     uint32   Timestamp (Unix seconds, LE) ← VERIFIED
11-13   3     bytes    Flags
14      1     uint8    SpO2 raw
15-18   4     bytes    Unknown
19      1     uint8    Heart Rate (BPM, direct value) ← VERIFIED (matched 184, 212, 71)
20      1     uint8    RR Interval Count
21-22   2     uint16   RR Interval 1 (ms, LE)
23-24   2     uint16   RR Interval 2 (ms, LE)
25-26   2     uint16   RR Interval 3 (ms, LE)
27+     49    bytes    Sensor data (different layout from Format A)
```

### Data Volume

- ~1 record per second when strap is active
- ~86,400 records per day
- Strap circular flash holds ~20 days of data (131K positions)
- Full Sync yields ~1M+ records at ~100% daily coverage
- Full Sync download rate: ~50 packets/second (~8-10 hours for full 20-day buffer)

Source: `WhoopDataDecoder.kt`, verified against raw BLE captures. Accel/gyro offsets verified with physical movement tests.

---

## BLE Connection Architecture

```
Phone (Android)                          Whoop 5.0 Strap
┌──────────────┐                        ┌──────────────┐
│ Official App │──BLE GATT──┐           │              │
│ (Whoop)      │            ├──────────►│  Maverick    │
│              │◄───────────┤           │  BLE Server  │
├──────────────┤            │           │              │
│ Our App      │──BLE GATT──┘           │  fd4b0001    │
│ (Capture)    │                        │  Service     │
│              │◄───────────────────────│              │
└──────────────┘                        └──────────────┘
```

Both apps can be installed simultaneously. The official Whoop app has full
BT permissions and operates normally alongside the companion app. Only one app can have an active
GATT connection at a time — the companion app connects on demand (Sync Now / Full Sync) and
disconnects when done. The official app handles ongoing BLE for cloud sync.

Key insight: The strap only responds to correctly AA01-framed commands with valid CRC16 (header) and CRC32 (payload). Sending raw unframed bytes is silently ignored.

---

## Device Info (Test Strap)

- **Model:** Whoop 5.0 (Maverick)
- **MAC:** <STRAP_MAC>
- **Serial:** <STRAP_SERIAL>
- **Device Name:** "Whoop"
- **User ID:** <USER_ID>

---

## Source Files (Decompiled APK)

Key classes from the Whoop APK (v24.x, ~96MB):

| File | Purpose |
|------|---------|
| `Po/p.java` | BLE UUID constants for all strap generations |
| `Po/o.java` | Strap generation enum (Gen4, Maverick, Puffin, Goose) |
| `Io/e.java` | Command code enum (80+ commands) |
| `Io/g.java` | PacketFrame base class (SOF = 0xAA) |
| `Jo/C8769e.java` | MaverickPacketFrame (CRC16 header validation) |
| `Jo/C8766b.java` | FramedPacket (CRC32 payload wrapping) |
| `Nl/AbstractC9459a.java` | Command packet assembly |
| `Qo/C9986c.java` | CRC-16 and CRC-32 implementations |
| `Qo/AbstractC9987d.java` | 4-byte alignment/padding utility |
| `nI/AbstractC16328e.java` | Main BLE manager |

---

## Authentication — Whoop Cloud API

### Cognito Login (No Root, No Secret Required)

The Whoop Android app authenticates via AWS Cognito through a proxy endpoint.

| Item | Value |
|------|-------|
| Auth Endpoint | `https://api.prod.whoop.com/auth-service/v3/whoop/` |
| Client ID | `<COGNITO_CLIENT_ID>` |
| Auth Flow | `USER_PASSWORD_AUTH` (no SECRET_HASH needed) |
| Region | `us-west-2` |
| User Pool | `us-west-2_<POOL_ID>` |
| Token Lifetime | 86400s (24h) |
| Token Scope | `aws.cognito.signin.user.admin` |
| User-Agent | `aws-sdk-kotlin/1.3.35 ...` |

**Login request:**
```bash
curl -X POST "https://api.prod.whoop.com/auth-service/v3/whoop/" \
  -H "Content-Type: application/x-amz-json-1.1" \
  -H "x-amz-target: AWSCognitoIdentityProviderService.InitiateAuth" \
  -d '{
    "AuthFlow": "USER_PASSWORD_AUTH",
    "AuthParameters": {"USERNAME": "email", "PASSWORD": "pass"},
    "ClientId": "<COGNITO_CLIENT_ID>"
  }'
```

**Response contains:** `AuthenticationResult.AccessToken`, `RefreshToken`, `IdToken`, `ExpiresIn`

**JWT claims in AccessToken:**
- `custom:user_id` — Whoop user ID (e.g. <USER_ID>)
- `custom:account_id` — Whoop account ID (e.g. `<ACCOUNT_ID>`)
- `exp` — expiry timestamp (auth_time + 86400)

**IMPORTANT:** The Cognito Client ID (extract from APK) does NOT require SECRET_HASH. A second client ID found in the JWT audience field DOES require a secret and cannot be used for direct auth.

### Token Refresh (Cognito REFRESH_TOKEN_AUTH)

Same endpoint and client ID as login:
```bash
curl -X POST "https://api.prod.whoop.com/auth-service/v3/whoop/" \
  -H "Content-Type: application/x-amz-json-1.1" \
  -H "x-amz-target: AWSCognitoIdentityProviderService.InitiateAuth" \
  -d '{
    "AuthFlow": "REFRESH_TOKEN_AUTH",
    "AuthParameters": {"REFRESH_TOKEN": "eyJjdHki..."},
    "ClientId": "<COGNITO_CLIENT_ID>"
  }'
```
Returns new AccessToken (24h) + IdToken. Refresh token is long-lived.

Legacy endpoint (also works):
```
POST auth-service/v2/whoop/refresh
Authorization: Bearer {refresh_token}
```

### API Headers Required for Mobile Endpoints

```
Authorization: Bearer {access_token}
x-whoop-app-version: 5.430.0
x-whoop-device-platform: ANDROID
x-whoop-strap-id: {serial_number}  (e.g. 5<STRAP_SERIAL>)
x-whoop-time-zone: {timezone}
```

---

## API Endpoints

### Developer API (basic data)
| Endpoint | Description |
|----------|-------------|
| `developer/v1/user/profile/basic` | User profile |
| `developer/v1/user/measurement/body` | Body measurements |
| `developer/v1/cycle?limit=25&nextToken=X` | All cycles (paginated) — strain, kJ, HR per day |
| `health-tab-bff/v1/health-tab` | Health overview (Whoop Age, etc.) |
| `rollups-service/v1/rollups/{user_id}?days=365` | Aggregated rollups |

### Mobile Deep-Dive API (detailed data)
| Endpoint | Params | Description |
|----------|--------|-------------|
| `home-service/v1/home` | `date=YYYY-MM-DD` | Full home screen (~78KB) |
| `home-service/v1/deep-dive/sleep` | `date=YYYY-MM-DD` | Sleep overview (~5KB) |
| `home-service/v1/deep-dive/sleep/last-night` | `date=YYYY-MM-DD` | Detailed sleep stages (~921KB) |
| `home-service/v1/deep-dive/sleep/trends` | `date=YYYY-MM-DD` | Sleep trends (~49KB) |
| `home-service/v1/deep-dive/strain` | `date=YYYY-MM-DD` | Strain details |
| `home-service/v1/deep-dive/strain/trends` | `date=YYYY-MM-DD` | Strain trends (~24KB) |
| `home-service/v1/deep-dive/recovery` | `date=YYYY-MM-DD` | Recovery details |
| `home-service/v1/deep-dive/recovery/trends` | `date=YYYY-MM-DD` | Recovery trends (~21KB) |
| `core-details-bff/v1/cardio-details` | `activityId=UUID` | Activity details |
| `core-details-bff/v1/activity-score-type` | — | Activity types |

### Deep-Dive Data Structure
```
sections[] → items[] → {type, content}
  type=SCORE_GAUGE → content.score_display, gauge_fill_percentage
  type=CONTRIBUTORS_TILE → content.metrics[] = [{title, status}]
  type=DETAILS_GRAPHING_CARD → content.arrow_stat[] = [{current_stat_text, historic_stat_text}]
```

Recovery metrics: HRV (ms), RHR (bpm), Respiratory Rate, Sleep Performance %
Sleep metrics: Hours vs Needed %, Sleep Consistency %, Sleep Efficiency %, High Sleep Stress %
Strain metrics: HR Zones 1-3, HR Zones 4-5, Strength Activity Time, Steps

---

## Tools

| File | Description |
|------|-------------|
| `parse_hci.py` | Parse Android btsnoop_hci.log, extract ATT operations |
| `decode_packets.py` | Decode AA01 packets, verify CRCs, display structure |
| `decode_to_csv.py` | Decode cached_packet_db blobs to CSV |
| `pull_data.sh` | ADB script to pull BLE logs and Whoop databases |
