# Whoop Maverick (5.0) Firmware — Reverse Engineering Report

Firmware: `maverick-50.35.2.0.zbin` (v50.35.2, Build 2025-11-04)
Target: Ambiq Apollo4 Blue Plus BGA SoC

---

## 1. File Format (.zbin)

The `.zbin` is Ambiq's **Secure OTA Container** format:

```
┌─────────────────────────────────────┐
│  512-byte Header (0x000 - 0x1FF)    │  Metadata, version, CRC
├─────────────────────────────────────┤
│  Gzip-compressed ARM binary         │  1,059,968 bytes compressed
│  (0x200 - EOF)                      │  → 1,548,504 bytes decompressed
└─────────────────────────────────────┘
```

### 1.1 Header Layout (512 bytes)

```
Offset  Size  Value               Field
------  ----  -----               -----
0x000   4     0xa4b443fc          CRC32 of gzip payload (verified ✓)
0x004   4     0x00102c80          Compressed payload size (1,059,968 bytes)
0x008   4     0x00000005          Algorithm flags (compression)
0x00C   4     0x00000005          Algorithm flags (encryption — none)
0x010   4     0x0000000d          Image type (0x0D = main application, secure)
0x014   4     0x00000000          Reserved
0x018   52    (see below)         Build info block
0x04C   16    "50.35.x.x"         Version string (null-terminated)
0x064   24    (redacted)          Builder machine name
0x07C   4     0x00000032 (50)     Version major
0x080   4     0x00000023 (35)     Version minor
0x084   4     0x00000002 (2)      Version patch
0x088   136   0x00...             Zero padding
0x110   4     0x00000005          (repeat of algo flags)
0x114   4     0x00102e80          Total image size incl. header
0x118   4     0x00102e7e          Image size - 2 (CRC offset?)
0x11C   4     0x00000200          Header size (512 bytes)
0x120   64    0x00...             Zero padding
0x160   160   0xFF...             Erased flash (0xFF fill)
0x1F8   4     0xbe9a3236          Header CRC32 = CRC32(bytes 0x008..0x1F7)
0x1FC   4     0xa4b443fc          Payload CRC32 copy (same as offset 0)
```

### 1.4 Three Verified Integrity Checks

1. **Payload CRC32** — `CRC32(zbin[0x200:EOF]) == 0xA4B443FC` stored at offsets 0x000 and 0x1FC
2. **Header CRC32** — `CRC32(zbin[0x008:0x1F8]) == 0xBE9A3236` stored at offset 0x1F8. Covers algo flags, image type, build info, version, MRAM fields. Excludes the payload CRC (first 8 bytes) and itself + CRC copy (last 8 bytes).
3. **Size consistency** — `payload_size(0x004) + header_size(0x11C) == total_size(0x114) == actual file size`

All three checks verified against the v50.35.2.0 firmware.

### 1.2 Build Info (offset 0x018)

```
Byte 0:     0x23 = '#' (marker)
Byte 1:     0x4A = 'J' (marker)
Byte 2:     0x0A = newline
Byte 3:     0x69 = 'i' (marker)
Bytes 4-27: "54fc551ae08a204f9d30ab17"  → Git commit hash (24 hex chars)
Bytes 28+:  "2025-11-04T18:46:59"       → Build timestamp (ISO 8601)
```

### 1.3 Gzip Payload

```
Gzip MTIME:         2025-11-04 19:48:09 (compression time)
Compression:        deflate
OS:                 0xFF (unknown/embedded)
Compressed size:    1,059,968 bytes (1.01 MB)
Decompressed size:  1,548,504 bytes (1.48 MB)
Compression ratio:  68.5%
```

---

## 2. ARM Binary Analysis

### 2.1 Memory Layout

The decompressed binary is an ARM Cortex-M4F image for the Ambiq Apollo4 Blue Plus.

```
Ambiq Apollo4 Blue Plus Memory Map:
  MRAM (Flash):  0x00018000 - 0x001FFFFF  (1.9 MB)
  SRAM:          0x10000000 - 0x101FFFFF  (2 MB)
  Peripherals:   0x40000000+

Firmware Image Layout:
  0x000 - 0x1FF:  OTA metadata (same as .zbin header, minus CRC prefix)
  0x200 - 0x20F:  ARM Cortex-M4 Vector Table
  0x210+:         Code (.text section)
  ~0x0A3B00:      Read-only data (.rodata) — strings, tables
  ~0x0C0000:      Const data / tables
  ~0x140000:      High entropy data (possibly compressed assets or crypto)
  ~0x17A4F8:      End of image (1,548,504 bytes total)
```

### 2.2 ARM Vector Table (offset 0x200)

```
Vector    Address       Handler
------    -------       -------
SP Init   0x10009C40    Stack Pointer → SRAM (40,000 bytes from base)
Reset     0x0004A4D9    Reset_Handler (Thumb)
NMI       0x000DF965    NMI_Handler
HardFault 0x000DF8E5    HardFault_Handler
MemMgmt   0x000DF905    MemManage_Handler
BusFault  0x000DF925    BusFault_Handler
UsageFlt  0x000DF945    UsageFault_Handler
SVCall    0x0004A541    SVC_Handler
PendSV    0x0004A541    PendSV_Handler (shared)
SysTick   0x000D5F41    SysTick_Handler
```

---

## 3. RTOS Architecture

The firmware runs a **QP (Quantum Platform) RTOS** — an event-driven active object framework. All subsystems are implemented as **Active Objects (AO)** communicating via asynchronous signals.

### 3.1 Active Objects (Tasks)

| Active Object | Source File | Purpose |
|---------------|-------------|---------|
| **Supervisor AO** | `./src/supervisor_ao.c` | System manager, error handling, reboot |
| **BLE AO** | `./src/ble.c` | Bluetooth Low Energy stack |
| **BLE Command AO** | `./modules/ble/src/ble_cmd_ao.c` | BLE command processing (AA01 protocol) |
| **Sensors AO** | `./modules/sensors/src/sensors_ao.c` | Sensor data collection & signal processing |
| **Flash AO** | `./src/flash.c` | Flash storage (historical data, circular buffer) |
| **Analytics AO** | `./modules/analytics/src/analytics_ao.c` | Sensor data analytics/signal processing |
| **I2C AO** | `./src/i2c_ao.c` | I2C bus driver (multi-bus) |
| **Listener AO** | `./modules/wpt/src/listener_ao.c` | WPT (Wireless Power Transfer) / charging |
| **Fuel Gauge AO** | `./modules/fuel_gauge/.../onsemi_fuel_gauge_ao.c` | Battery management (LC709205F) |
| **UI Manager AO** | `./modules/ui_subsystem/src/ui_manager_ao.c` | UI orchestration (LED + haptics) |
| **LED UI AO** | `./modules/ui_subsystem/src/led_ui_ao.c` | LED patterns (LP5562 driver) |
| **Haptics AO** | `./modules/ui_subsystem/src/haptics_ao.c` | Haptic feedback (DRV2625 driver) |
| **Temp Sensors AO** | `./src/temp_sensors_ao.c` | Temperature sensing (AS6221) |
| **Tag Reader AO** | `./modules/tag_reader/src/tag_reader_ao.c` | RFID/NFC reader |
| **ECG Control AO** | `./modules/ecg_control/src/ecg_control_ao.c` | ECG recording |
| **Debug Menu AO** | `./modules/debug_menu/src/debugmenu_ao.c` | UART debug console |
| **ITEST AO** | `./src/itest_ao.c` | Integration/manufacturing tests |
| **Cordio AO** | `./src/whoop_cordio/whoop_cordio_ao.c` | Cordio BLE stack integration |

### 3.2 Signal Count by Subsystem

| Subsystem | Signal Count | Key Functionality |
|-----------|-------------|-------------------|
| LISTENER (WPT/Charging) | 67 | Charging, power management, OEM updates |
| FUEL_GAUGE | 48 | Battery SOC, voltage, current, temperature |
| BLE | 44 | Connection, advertising, data transfer |
| SENSORS | 27 | IMU, PPG, SpO2, AFE data |
| FLASH | 25 | Data storage, trim, erase, debug |
| DRV2625 (Haptics IC) | 18 | Haptic sequences, patterns |
| LC709205F (Fuel Gauge IC) | 18 | Battery chemistry readings |
| HAPTICS | 18 | High-level haptic control |
| UI | 15 | Alarm, display SOC, haptic notifications |
| AS6221 (Temp Sensor) | 15 | Skin temperature readings |
| LP5562 (LED Driver) | 12 | LED patterns, status indication |
| I2C | 12 | Bus management, error recovery |
| TAG_READER | 10 | RFID/NFC tag detection |
| SUPERVISOR | 10 | System monitoring, error log |
| TEMP_SENSORS | 10 | Temperature subsystem |
| DEBUGMENU | 9 | UART debug interface |
| ANALYTICS | 7 | Signal processing version |
| LED_UI | 8 | LED UI patterns |
| ITEST | 6 | Manufacturing tests |
| ECG | 3 | ECG recording control |

---

## 4. Hardware Components

Based on firmware string analysis:

| IC | Manufacturer | Function | I2C Address | Notes |
|----|-------------|----------|-------------|-------|
| **Apollo4 Blue Plus** | Ambiq | Main MCU (ARM Cortex-M4F) | — | BLE integrated, ultra-low power |
| **ICM-45686** | TDK InvenSense | 6-axis IMU (Accel + Gyro) | — | Latest generation MEMS |
| **LP5562** | Texas Instruments | RGB LED Driver | — | 4-channel PWM |
| **DRV2625** | Texas Instruments | Haptic Driver | — | Piezo/LRA support |
| **LC709205F** | ON Semiconductor | Fuel Gauge | — | Li-Ion battery monitor |
| **AS6221** | ams-OSRAM | Skin Temperature | — | ±0.09°C accuracy |
| *AFE (unnamed)* | (likely Maxim) | Analog Front-End | — | PPG/SpO2/ECG |
| *RFID/NFC reader* | (unknown) | Tag Reader | — | Accessory detection? |

### 4.1 Sensor Capabilities

- **PPG** (Photoplethysmography) — Heart rate, HRV (RR intervals)
- **SpO2** — Blood oxygen saturation
- **ECG** — Electrocardiogram (raw + filtered modes)
- **IMU** — Accelerometer + Gyroscope (ICM-45686)
- **Skin Temperature** — AS6221 thermometer
- **Ambient Temperature** — Via fuel gauge
- **Wear Detection** — Optical-based (PPG signal quality)

---

## 5. BLE Commands (from firmware strings)

These are the BLE commands implemented in the firmware (sent via the AA01 protocol on the CMD_TO_STRAP characteristic):

| Command | Description |
|---------|-------------|
| `BLE_CMD_SET_CLOCK` | Set strap RTC |
| `BLE_CMD_GET_ALARM_TIME` | Query alarm settings |
| `BLE_CMD_SET_ALARM_INFO` | Configure alarm |
| `BLE_CMD_ALARM_DISABLE` | Disable alarm |
| `BLE_CMD_SET_REALTIME_HR` | Enable/disable real-time HR streaming |
| `BLE_CMD_GET_ADV_NAME` | Get advertising name |
| `BLE_CMD_ADV_NAME_SET` | Set advertising name |
| `BLE_CMD_FORGET_BONDING` | Clear BLE bond |
| `BLE_CMD_RAW_DATA_STOP` | Stop raw data stream |
| `BLE_CMD_IMU_SET_DATA_STREAM` | Toggle IMU data streaming |
| `BLE_CMD_HAPTICS_RUN_NTF` | Trigger haptic notification |
| `BLE_CMD_HAPTICS_STOP` | Stop haptics |
| `BLE_CMD_HISTORY_ENABLE_HIGH_FREQ` | Enable high-frequency history |
| `BLE_CMD_CONFIG_VALUE_SET_DEVICE_CONFIG` | Set device configuration |
| `BLE_CMD_START_DEVICE_CONFIG_KEY_EX` | Start config key exchange |
| `BLE_CMD_SEND_NEXT_DEVICE_CONFIG` | Continue config transfer |
| `BLE_CMD_BODY_LOC_GET_STATUS` | Body location status |
| `BLE_CMD_SIGPROC_SET_WRIST_DETECT` | Set wrist detection |
| `BLE_CMD_UART_ENABLE` | Enable UART debug |
| `BLE_CMD_UART_DISABLE` | Disable UART debug |
| **Firmware Update:** | |
| `BLE_CMD_START_FIRMWARE_LOAD` | Begin OTA firmware load |
| `BLE_CMD_LOAD_FW_DATA` | Send firmware data chunk |
| `BLE_CMD_PROCESS_FIRMWARE_IMAGE` | Process/validate firmware |
| `BLE_CMD_VERIFY_FW_IMAGE` | Verify firmware integrity |
| **ECG:** | |
| `BLE_CMD_ECG_MAIN_CONTROL` | Start/stop ECG recording |
| `BLE_CMD_ECG_SELECT_WRIST` | Select wrist for ECG |
| `BLE_CMD_ECG_SAVE_RAW` | Save raw ECG data |
| `BLE_CMD_ECG_SAVE_FILTERED` | Save filtered ECG |
| `BLE_CMD_ECG_SEND_RAW` | Stream raw ECG over BLE |

---

## 6. Firmware Update via BLE (OTA)

The firmware contains a **custom OTA update path** distinct from the Nordic DFU used for the PUFFIN. The Maverick uses Ambiq's native OTA over the Whoop BLE protocol:

### 6.1 OTA BLE Flow

```
1. App sends BLE_CMD_START_FIRMWARE_LOAD
   → Strap prepares flash for update

2. App sends BLE_CMD_LOAD_FW_DATA (repeated)
   → Chunks of firmware binary sent via AA01 protocol
   → Strap writes chunks to update flash region

3. App sends BLE_CMD_PROCESS_FIRMWARE_IMAGE
   → Strap processes/validates the complete image

4. App sends BLE_CMD_VERIFY_FW_IMAGE
   → Strap verifies CRC/integrity

5. Strap reboots into new firmware
```

### 6.2 Why Both Nordic DFU AND Ambiq OTA?

The app uses **two different update paths**:

- **Nordic DFU** (via `DfuServiceInitiator`): Used for the **Nordic BLE co-processor** firmware (PUFFIN, HARVARD-NORDIC). Uses standard Nordic DFU protocol with `.bin` files and DFU bootloader.

- **Ambiq OTA** (via BLE commands): Used for the **main Ambiq MCU** firmware (MAVERICK/GOOSE). Uses the AA01 command protocol with `.zbin` files.

The official app orchestrates both — the `firmware_zip_file` ZIP may contain files for both chips.

---

## 7. Debug Interface

The firmware has an extensive **UART debug console** accessible via the `DEBUGMENU` active object:

### 7.1 Debug Menu Commands (partial)

```
  b     Show bootloader firmware version and build meta data
  v     Show main firmware versions and build meta data
  m     Show main firmware version in MFG format
  M     Reboot NOW using soft reset request GPIO
  B     Run flash connectivity test (read device ID)
  N     Hard erase entire flash device (Advanced)
  e <n> Enable/Disable flash AO
  s     Show flash status
  o     Perform listener OEM parameter update
  c     Clear the reboot counts stored in NVM
  r     Show memfault reboot reason
  0     Set RTC to 2000 (disable flash writes)
  1     Set RTC to 2011 (enable flash writes, old timestamps)
  y     Flash fuel gauge battery profile for ATE
  z     FORCE flash fuel gauge battery profile for ATE
```

### 7.2 Enabling UART Debug

Via BLE commands:
- `BLE_CMD_UART_ENABLE` — Activates UART debug output
- `BLE_CMD_UART_DISABLE` — Deactivates UART

The debug interface can also be accessed by the BLE console log packets (type 0x32).

---

## 8. Memfault Integration

The firmware integrates **Memfault** crash reporting:

- Signal: `MFLT_DATA_SEND_SIG`
- BLE: `BLE_REQUEST_MFLT_ENABLED`
- Debug: `r — Show memfault reboot reason`

Memfault data is sent via BLE using the Memfault characteristic (UUID `fd4b0007-...`).

---

## 9. Entropy / Section Map

```
Region              Entropy   Content Type
0x000000-0x020000   6.04      ARM code (.text) — startup, vector table
0x020000-0x0A0000   6.77-6.96 ARM code (.text) — main application
0x0A0000-0x0C0000   6.74      ARM code + rodata — strings, signal tables
0x0C0000-0x0E0000   4.75      Const tables — lower entropy, lookup data
0x0E0000-0x120000   7.38-7.45 Mixed code + compressed/encrypted data
0x140000-0x160000   7.95      High entropy — possibly encrypted keys or compressed assets
0x160000-0x17A4F8   6.35      Tail code + padding
```

---

## 10. Key Findings Summary

1. **Format**: `.zbin` = 512-byte Ambiq header + gzip-compressed ARM Cortex-M4F binary
2. **Chip**: Ambiq Apollo4 Blue Plus (BGA) — ultra-low-power ARM Cortex-M4F with integrated BLE
3. **RTOS**: QP (Quantum Platform) active object framework — 18+ active objects
4. **Sensors**: ICM-45686 (IMU), PPG/SpO2 AFE, ECG, AS6221 (skin temp), LC709205F (fuel gauge)
5. **UI**: LP5562 (RGB LED), DRV2625 (haptics)
6. **OTA**: Uses Ambiq-native OTA via BLE commands (not Nordic DFU) for main MCU
7. **Debug**: Full UART debug console accessible via BLE commands
8. **Build**: Git `54fc551ae08a204f9d30ab17`, build date 2025-11-04
9. **No encryption**: The firmware payload is gzip-compressed but NOT encrypted — fully readable
10. **RFID/NFC**: Tag reader subsystem present — likely for accessory/charger detection

---

## 11. Deep Disassembly Findings (ARM Cortex-M4F Thumb2)

### 11.1 Vector Table Analysis (offset 0x200)

```
Vector    Address      Handler
------    --------     -------
SP Init   0x10009C40   (SRAM top)
Reset     0x0004A4D9   Main entry point
NMI       0x000DF965   Dedicated handler
HardFault 0x000DF8E5   Dedicated handler
MemManage 0x000DF905   Dedicated handler
BusFault  0x000DF925   Dedicated handler
UsageFault 0x000DF945  Dedicated handler
SVCall    0x0004A541   Default handler (shared)
PendSV    0x0004A541   Default handler (shared)
SysTick   0x000D5F41   Dedicated handler
IRQ1      0x00078C15   (likely UART/SPI)
IRQ17     0x000B8DCD   (likely BLE interrupt)
IRQ0-47   0x0004A541   Default handler (most IRQs share one handler)
```

Only 5 IRQs have dedicated handlers — the rest use QP's centralized interrupt dispatcher.

### 11.2 Language Confirmation: **C**

Evidence:
- All source paths use `.c` extension: `./modules/ble/src/ble_cmd_ao.c`, `./modules/sensors/src/sensors_ao.c`, `./src/flash.c`
- QP framework is C-based (uses Active Object pattern without C++ classes)
- No C++ vtable patterns, no Rust panic handlers, no Go runtime strings
- Printf-style format strings throughout (`%6llu: %s:` prefix pattern)

### 11.3 CRC Implementation

**CRC16** — Software function at binary offset `0x0127D8`, uses 256-entry lookup table at `0x0AC3B4`. Reflected CRC-16/MODBUS with init `0xFFFF`. Wrapper at `0x0127FC`. Called from 9 locations for AA01 frame header validation.

**CRC32** — **Hardware-computed** via Ambiq MSPI DMA at function `0x012804`. No software CRC32 lookup table exists in the firmware — this is why no CRC32 polynomial table was found during scanning. Used for AA01 frame payload validation and firmware image verification.

### 11.4 Security Model — Disassembly Evidence

**No cryptographic signature verification in application firmware:**
- Zero references to RSA, ECDSA, AES, SHA-256, or any crypto algorithm names
- No key material references (no `otp_key`, `customer_key`, `fuse`, `revoke`)
- "Strap signature" is a **plain text string**, not a crypto signature:
  - `g           Get strap signature` (debug console command)
  - `h <string>  Set strap signature` (takes arbitrary string)
- The only integrity check is **CRC32**:
  - `CRC of update image passed` / `CRC of update image failed`
  - `Update image CRC valid: %s`

**SBL (Secondary Bootloader) is separate:**
- `Bootloader Ver: %u.%u.%u.%u`
- `SBL Ver: V1` / `SBL Ver: V2`
- SBL lives in ROM/OTP — not part of the updatable firmware
- SBL **may** enforce crypto verification, but this cannot be determined from the application firmware alone

**Cooper (BLE Radio) has its own auth:**
- `BLE Controller FW Auth Passed, Continue with FW`
- `BLE Controller SBL Error 0x%x`
- `Clear Cooper Signature, reset Cooper and talk with SBL again`
- Cooper has a separate SBL with signature validation

### 11.4 Update Flash Operations

```
Update Flash: read ID MFG 0x%x Dev 0x%x Dev 0x%x
Update Flash: ISSI 64Mb NOR detected
Update Flash: Winbond 64Mb NOR detected
Update Flash: Chip not recognized or detected
Update Flash: Write failed at addr 0x%x
Update Flash: Read failed at addr 0x%x
Update Flash: Memory compare failed at addr 0x%x
Update Flash: Failed to start program timeout %d
Update Flash: Failed to start erase timeout %d
```

Write-verify pattern: every chunk written to NOR flash is read back and compared. This is the safety net during the transfer phase.

### 11.5 BLE Firmware Command Codes (Internal vs External)

| External (AA01) | Internal (FW) | String Evidence |
|-----------------|---------------|-----------------|
| 80 (0x50) | 0x8E (142) | `Command Start Firmware Load (0x8E)` |
| 81 (0x51) | 0x8F (143) | `Command Load FW Data` |
| 82 (0x52) | 0x90 (144) | `Command Process FW Image (0x90)` |
| 83 (0x53) | 0x91 (145) | `Verify FW image chunk offset` |

### 11.6 Cooper BLE Radio Update Flow

The Cooper BLE5 radio has an independent firmware update path:
```
1. Check version: "Cooper's firmware version requested before initialization"
2. Version mismatch: "Cooper has the incorrect firmware version. Expected %d.%d.%d.%d"
3. Trigger upgrade: "Received new BLE Controller FW version = %d.%d.%d.%d Going for upgrade"
4. Auth check: "BLE Controller FW Auth Passed, Continue with FW"
5. Transfer: "BLE controller upgrade in progress, wait..."
6. Complete: "BLE Controller Init Done"
7. Error: "BLE Controller SBL Error 0x%x"
8. Recovery: "Clear Cooper Signature, reset Cooper and talk with SBL again"
```

### 11.7 Fast Recovery Mode

```
Fast Recovery Mode set to: %d
```

This suggests the firmware has a recovery mode that can be toggled — potentially useful for OTA recovery scenarios.

### 11.8 No Anti-Rollback Protection

- No `monotonic_counter`, `revocation`, or `anti-rollback` strings
- The firmware API server always returns the latest version
- No evidence of version comparison/enforcement in the firmware binary
- Downgrade may be possible if older firmware images are obtained
