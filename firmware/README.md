# Whoop 5.0 Firmware Reverse Engineering

Complete reverse engineering toolkit for the Whoop 5.0 ("Maverick") fitness band firmware.

**Target**: Whoop 5.0 — Ambiq Apollo4 Blue Plus SoC (ARM Cortex-M4F, 96 MHz, BLE 5.1)

## What We Found

The Whoop 5.0 runs a **1.55 MB ARM Cortex-M4F** firmware on an Ambiq Apollo4 Blue Plus SoC. It uses **QP RTOS** (Quantum Platform) — an event-driven active object framework (not FreeRTOS/Zephyr). The firmware contains **13,000+ functions** and **20,000+ embedded strings** that reveal the complete architecture.

### Hardware (from firmware analysis)

| Component | Chip | Interface | Purpose |
|---|---|---|---|
| SoC | Ambiq Apollo4 Blue Plus | — | Main processor (Cortex-M4F + BLE) |
| IMU | TDK ICM-45686 | I2C 0x68/0x69 | 6-axis accelerometer + gyroscope |
| Temperature | ams AS6221 | I2C 0x47/0x48 | Skin temperature (±0.09°C) |
| Fuel Gauge | OnSemi LC709205F | I2C 0x0B/0x55 | Battery level, voltage, current |
| RGB LEDs | TI LP5562 | I2C 0x30 | Status LEDs, patterns |
| Haptic Motor | TI DRV2625 | I2C 0x5A | Vibration feedback |
| PPG/AFE | Unknown (likely Maxim) | SPI | Heart rate, SpO2, ECG |
| NFC/RFID | Unknown | Unknown | Accessory detection |
| BLE Radio | Ambiq Cooper (integrated) | Internal | Cortex-M0 BLE controller |
| External Flash | ISSI/Winbond 64Mbit | SPI | Firmware staging area |

### Software Architecture

The firmware is written in **C** and uses 24 Active Objects (tasks):

```
┌───────────────────────────────────────────────────────────────┐
│                 QP RTOS (Active Object Framework)              │
│                                                                 │
│  Supervisor ─── BLE_Command ─── Whoop_Cordio ─── Sensors      │
│  (system mgr)  (AA01 protocol) (BLE stack)      (PPG/IMU)    │
│                                                                 │
│  Analytics ──── Flash ───────── I2C ──────────── Listener     │
│  (HR/SpO2/HRV) (circular buf)  (multi-bus)      (WPT/charge) │
│                                                                 │
│  Fuel_Gauge ─── LC709205F ──── Temp_Sensors ──── AS6221       │
│  (battery mgr)  (chip driver)  (temp logic)      (chip driver)│
│                                                                 │
│  UI_Manager ─── LED_UI ──────── LP5562 ──────── Haptics       │
│  (orchestrator) (patterns)      (chip driver)    (vibration)  │
│                                                                 │
│  DRV2625 ────── Tag_Reader ──── ECG_Control ──── Debug_Menu   │
│  (chip driver)  (RFID/NFC)     (ECG recording)  (UART debug) │
└───────────────────────────────────────────────────────────────┘
```

**394 inter-AO signals**, Listener (charging) has the most complex state machine (26 states).

### Security Findings

| Finding | Severity | Details |
|---|---|---|
| CRC32-only firmware authentication | **HIGH** | No RSA/ECDSA/AES signatures in application firmware |
| No crypto library | **HIGH** | No mbedTLS, wolfSSL, or similar found |
| BLE-controllable UART debug | MEDIUM | `WSBLE_CMD_UART_ENABLE/DISABLE` |
| Extensive debug menu | MEDIUM | 14 categories, reset/erase commands |
| No anti-rollback | LOW | No version enforcement for main firmware |
| No MPU/stack canaries | LOW | No memory protection or overflow detection |
| Cooper BLE has own auth | INFO | BLE controller firmware is authenticated via SBL |

**Unknown**: The Secondary Boot Loader (SBL) in ROM *may* enforce cryptographic checks. This cannot be determined from application firmware analysis alone.

## Directory Structure

```
firmware/
│
├── WHOOP_FIRMWARE_REPORT.md          # ★ Main report (German, human-readable)
├── README.md                          # This file
│
├── firmware_downloader.py             # Download firmware from Whoop API
│
├── analysis/                          # Automated firmware analysis
│   ├── common.py                      # Shared utilities (capstone, r2, angr)
│   ├── track_a_disassembly.py         # Function discovery + call graph
│   ├── track_b_strings.py             # String extraction + categorization
│   ├── track_c_peripherals.py         # Peripheral/sensor driver analysis
│   ├── track_d_algorithms.py          # FPU region + algorithm extraction
│   ├── track_e_rtos.py                # QP RTOS architecture recovery
│   ├── track_f_security.py            # Security assessment
│   ├── generate_report.py             # HTML report from all track JSONs
│   └── output/                        # Generated JSONs (gitignored, ~16 MB)
│
├── tools/                             # Firmware manipulation tools
│   ├── zbin_builder.py                # Build/verify/extract .zbin containers
│   ├── firmware_diff.py               # Compare firmware binaries (HTML report)
│   └── firmware_patcher.py            # Patch firmware + recalculate CRCs
│
├── custom_firmware/                   # Proof-of-concept custom firmware
│   ├── main.c                         # "Hello World" UART output
│   ├── startup.c                      # Vector table + boot init
│   ├── linker.ld                      # Apollo4 memory layout
│   └── Makefile                       # ARM cross-compilation
│
├── analyze_*.py                       # Earlier analysis scripts (iterative)
├── fw_validation_analysis.py          # Original validation analysis
│
├── FIRMWARE_UPDATE_DOCUMENTATION.md   # API + APK reverse engineering
├── MAVERICK_FIRMWARE_REVERSE_ENGINEERING.md  # Binary analysis details
└── SAFE_FIRMWARE_UPDATE_GUIDE.md      # BLE OTA protocol specification
```

## Getting Started

### Prerequisites

```bash
# Python packages
pip install capstone r2pipe angr keystone-engine

# ARM cross-compiler (for custom firmware)
sudo apt install gcc-arm-none-eabi

# Radare2 (for function analysis)
# https://github.com/radareorg/radare2
```

### 1. Download Firmware

```bash
# Login and download latest firmware for all device types
python3 firmware_downloader.py --email you@email.com

# Or use existing token
python3 firmware_downloader.py --token eyJjdHki...

# Check only (no download)
python3 firmware_downloader.py --token TOKEN --check-only
```

This downloads firmware from `api.prod.whoop.com/firmware-service/v4/` and extracts `.zbin` → `.bin`.

### 2. Run Analysis

```bash
cd analysis

# Run all 6 tracks (requires firmware binary in expected location)
python3 track_a_disassembly.py    # ~5 min (radare2 + angr)
python3 track_b_strings.py        # ~1 sec
python3 track_c_peripherals.py    # ~10 sec
python3 track_d_algorithms.py     # ~30 sec
python3 track_e_rtos.py           # ~5 sec
python3 track_f_security.py       # ~10 sec

# Generate HTML master report from all JSONs
python3 generate_report.py
# → firmware_analysis_report.html
```

### 3. Verify/Build .zbin Files

```bash
cd tools

# Verify an existing .zbin (checks all 3 CRCs)
python3 zbin_builder.py --verify ../maverick_ambiq_50.35.2.0/maverick-50.35.2.0.zbin

# Build a .zbin from a raw binary
python3 zbin_builder.py --build input.bin --output output.zbin --version 99.0.1.0

# Extract .bin from .zbin
python3 zbin_builder.py --extract firmware.zbin --output firmware.bin
```

### 4. Build Custom Firmware

```bash
cd custom_firmware
make                    # Compile with arm-none-eabi-gcc
make zbin               # Package as .zbin (uses ../tools/zbin_builder.py)
make verify             # Verify the .zbin CRCs
make clean              # Clean build artifacts
```

Output: 668-byte ARM binary that outputs "Hello from custom Whoop firmware!" on UART.

### 5. Compare Firmware Versions

```bash
cd tools
python3 firmware_diff.py old.bin new.bin --output diff_report.html
```

### 6. Patch Firmware

```bash
cd tools

# NOP out a function call
python3 firmware_patcher.py input.bin --patch 0x1234:nop:4 --output patched.bin

# Change a string
python3 firmware_patcher.py input.bin --patch 0xABCD:str:"Hello" --output patched.bin

# Patch and repackage as .zbin
python3 firmware_patcher.py input.bin --patch 0x1234:nop:4 --zbin --output patched.zbin
```

## .zbin File Format

The Ambiq OTA container format used for firmware updates:

```
Offset  Size  Field                    Example (v50.35.2.0)
──────  ────  ─────────────────────    ────────────────────
0x000   4     Payload CRC32            0xA4B443FC
0x004   4     Compressed payload size  1,059,968
0x008   4     Compression (0x05=gzip)  0x05
0x00C   4     Encryption (0x05=none)   0x05
0x010   4     Image type (0x0D=app)    0x0D
0x04C   16    Version string           "50.35.x.x"
0x064   24    Builder name             (redacted)
0x07C   4     Major version            50
0x080   4     Minor version            35
0x084   4     Patch version            2
0x1F8   4     Header CRC32             0xBE9A3236
0x1FC   4     Payload CRC32 (copy)     0xA4B443FC
0x200+  var   gzip-compressed payload  (ARM Cortex-M4F binary)
```

**Three integrity checks** (all must pass):
1. `CRC32(zbin[0x200:]) == zbin[0x000] == zbin[0x1FC]`
2. `CRC32(zbin[0x008:0x1F8]) == zbin[0x1F8]`
3. `zbin[0x004] + 512 == file_size`

## BLE Protocol (AA01)

All communication uses a proprietary framing protocol:

```
┌──────┬──────┬──────────┬─────────┬────────┬──────┬────────────┬───────┐
│ 0xAA │ 0x01 │ Length   │ Routing │ CRC16  │ Type │ Payload    │ CRC32 │
│  1B  │  1B  │  2B LE   │  2B     │ 2B LE  │  1B  │ variable   │  4B   │
└──────┴──────┴──────────┴─────────┴────────┴──────┴────────────┴───────┘
```

- **CRC-16/MODBUS** (reflected, init=0xFFFF) protects the 6-byte header
- **CRC32** protects the entire payload
- All payloads are 4-byte aligned

**29 known BLE commands** (from firmware strings):
`SET_CLOCK`, `SET_REALTIME_HR`, `IMU_SET_DATA_STREAM`, `HAPTICS_RUN_NTF`, `HAPTICS_STOP`, `START_FIRMWARE_LOAD`, `LOAD_FW_DATA`, `PROCESS_FIRMWARE_IMAGE`, `VERIFY_FW_IMAGE`, `ECG_MAIN_CONTROL`, `ECG_SELECT_WRIST`, `ECG_SEND_RAW`, `UART_ENABLE`, `UART_DISABLE`, `FORGET_BONDING`, `SET_ALARM_INFO`, `GET_ALARM_TIME`, `ALARM_DISABLE`, `ADV_NAME_SET`, `GET_ADV_NAME`, `SIGPROC_SET_WRIST_DETECT`, `HISTORY_ENABLE_HIGH_FREQ`, `BODY_LOC_GET_STATUS`, `RAW_DATA_STOP`, `CONFIG_VALUE_SET_DEVICE_CONFIG`, `START_DEVICE_CONFIG_KEY_EX`, `SEND_NEXT_DEVICE_CONFIG`, `ECG_SAVE_FILTERED`, `ECG_SAVE_RAW`

## How It Works (Methodology)

### String Recovery

C compilers embed `__FILE__` paths in binaries when code uses `assert()`, logging macros, or `Q_ASSERT` (QP RTOS). The Whoop firmware logs extensively:

```c
// In the source code:
LOG("%6llu: %s: Device disabled", timestamp, __FILE__);
// Becomes this string in the binary at offset 0x0B5BF5:
"./modules/ble/src/ble_cmd_ao.c"
```

This reveals the **complete project structure**: 24 Active Object source files, module organization, build system (Jenkins CI/CD with GCC 10).

### Function Discovery

Three approaches combined:
1. **radare2** `aaa` auto-analysis: 3,210 functions
2. **angr** CFGFast recovery: 9,832 additional functions
3. **Capstone** prolog scanning: `PUSH {r4-r7, lr}` pattern matching

### Sensor Identification

I2C device addresses (stored as constants in driver code) cross-referenced with known chip datasheets. All 7 I2C devices identified by address + reference count.

### Algorithm Location

FPU instructions (`VLDR`, `VMUL`, `VSQRT`, `VCMPE`) are rare in embedded code. Scanning for FPU instruction clusters identifies algorithm regions. The densest region (231 FPU ops in 2 KB at `0x0602AE`) is the SigProc signal processing core.

## Open Questions

1. **Will the SBL accept unsigned firmware?** The application firmware has no crypto, but the ROM bootloader might. Testable by attempting an OTA with the custom firmware.

2. **What is the PPG/AFE chip?** The most important sensor (HR/SpO2) is unidentified. Likely a Maxim/Analog Devices part. Could be determined by opening the strap or finding FCC filing photos.

3. **What are the exact GPIO assignments?** Pin mapping is not in the firmware strings. Would require SBL dump or hardware probing.

4. **Can Zephyr RTOS run on this hardware?** Ambiq Apollo4 has Zephyr support in-tree. Combined with open-source BLE and sensor drivers, a fully custom open-source firmware is theoretically possible.

## Related Projects

- **InfiniTime** — Open-source firmware for PineTime watch (similar concept, different hardware)
- **Gadgetbridge** — Open-source Android app for fitness trackers
- **Ambiq SDK** — Official Apollo4 development kit (publicly available)
- **Zephyr RTOS** — Open-source RTOS with Apollo4 support

## Legal Note

This project is for **educational and research purposes**. Firmware binaries are proprietary and not included in this repository. Use the `firmware_downloader.py` with your own Whoop account credentials to obtain firmware for personal analysis.
