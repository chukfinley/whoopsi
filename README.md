# Whoopsi

Open-source tools for extracting and analyzing raw sensor data from Whoop 4.0/5.0 fitness bands.

Reverse-engineered BLE protocol, custom Android companion app, alternative Flutter client, Python CLI for cloud data export, and scoring algorithms that reproduce Whoop's Recovery/Sleep/Strain scores with MAE 2.76.

> **Legal basis:** EU Directive 2009/24/EC Article 6 + German UrhG &sect;69e (reverse engineering for interoperability). No proprietary code is distributed &mdash; only analysis tools and findings.

---

## Components

| Component | What it does | Status |
|-----------|-------------|--------|
| [**ble-sync**](ble-sync/) | Android app that connects to the Whoop strap via BLE and downloads raw sensor data (HR, SpO2, accelerometer, gyroscope) at 1-second resolution | Working |
| [**app**](app/) | Flutter app &mdash; alternative client for the Whoop band with 22 screens, cloud sync, BLE connection | Experimental |
| [**cli**](cli/) | Python CLI that downloads your data from the Whoop cloud API as JSON files (sleep stages, recovery, strain, trends) | Working |
| [**algorithms**](algorithms/) | Python scoring algorithms that reproduce Whoop's daily scores from raw sensor data | Working |
| [**firmware**](firmware/) | Firmware reverse engineering toolkit for the Whoop 5.0 (Ambiq Apollo4 Cortex-M4F) | Research |

---

## How It Works

```
Whoop 5.0 Strap                    Your Phone                         Your Computer
 (BLE)                              (Android)                          (Python)
   |                                    |                                  |
   |--- BLE sensor data (AA01) ------->|                                  |
   |    HR, SpO2, accel, gyro          | ble-sync                         |
   |    1 record/sec, ~20 days buffer  | stores in SQLite                 |
   |                                    |                                  |
   |                                    |--- adb pull db --------------->|
   |                                    |                                  | algorithms/
   |                                    |                                  | analyze_all.py
   |                                    |                                  |
   |                                    | Whoop Cloud API                  |
   |                                    |--- cli ----------------------->|
   |                                    |    login, export, deep-dive      | JSON files
   |                                    |                                  |
   |                                    | app (Flutter)                    |
   |                                    | alternative client app           |
```

---

## Quick Start

### 1. Download raw sensor data via BLE

Build and install the BLE sync app:

```bash
cd ble-sync
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the app, tap "Sync Now" to download data from your strap. The strap's circular buffer holds ~20 days of 1-second-resolution sensor data.

Pull the database to your computer:

```bash
adb shell "run-as com.whoopcapture cat databases/whoop_capture.db" > whoop_capture.db
```

See [ble-sync/README.md](ble-sync/) for details on the BLE protocol and sync modes.

### 2. Download cloud data via CLI

```bash
pip install -e ./cli
export WHOOP_COGNITO_CLIENT_ID="<your-client-id>"  # extract from Whoop APK

whoop login --email you@email.com
whoop deep-dive --date all    # downloads per-day sleep stages, recovery, strain
whoop export --output backup  # full data export
```

See [cli/README.md](cli/) for all commands and authentication options.

### 3. Run scoring algorithms

```bash
cp whoop_capture.db algorithms/data/raw/
cd algorithms
pip install numpy scipy scikit-learn
python analyze_all.py            # generates full_dashboard.html
```

See [algorithms/README.md](algorithms/) for algorithm details.

---

## Authentication

All tools use AWS Cognito (same auth flow as the official Whoop app). You need:

1. Your Whoop **email and password**
2. The **Cognito Client ID** (extract from the official Whoop APK by decompiling it and searching for `ClientId` in the auth flow)

Set the client ID as an environment variable:

```bash
export WHOOP_COGNITO_CLIENT_ID="<your-client-id>"
```

Tokens are saved locally at `~/.whoop/token.json` and auto-refresh (24h access token, long-lived refresh token).

---

## BLE Protocol Summary

The Whoop strap uses a proprietary BLE protocol with AA01 framing:

```
[0xAA][0x01][length:2][routing:2][CRC16:2][type:1][payload:var][CRC32:4]
```

- CRC-16/MODBUS (reflected, init=0xFFFF) protects the header
- Standard CRC32 protects the payload
- All payloads are 4-byte aligned
- 40+ commands documented, 29 confirmed from firmware strings

Key commands: `SEND_HISTORICAL_DATA` (0x16), `HISTORICAL_DATA_RESULT` (0x17), `GET_DATA_RANGE` (0x22), sensor packets (0x2F).

Full protocol documentation: [ble-sync/CLAUDE.md](ble-sync/CLAUDE.md)

---

## Project Structure

```
whoopsi/
  ble-sync/                # Android BLE sensor capture app (Kotlin/Jetpack Compose)
    app/src/main/java/     #   BLE service, protocol, data decoder, charts
    data/scripts/          #   Python export & dashboard scripts

  app/                     # Flutter alternative client app
    lib/services/          #   BLE, API, AI, weather, hydration, journal
    lib/screens/           #   22 screens (home, sleep, recovery, strain, ...)
    lib/widgets/           #   Score gauges, charts, cards

  cli/                     # Python CLI for cloud data export
    whoop_cli/commands/    #   login, status, export, deep-dive, dashboard

  algorithms/              # Scoring & analysis
    algo1_custom/          #   Rule-based (HR zones + EPOC)
    algo4_calibrated/      #   Whoop-calibrated formulas (MAE 2.76)
    algo5_ml/              #   ML sleep phase classifier (74.7% accuracy)

  firmware/                # Firmware RE toolkit
    analysis/              #   6 analysis tracks (disassembly, strings, peripherals, ...)
    tools/                 #   zbin builder, firmware diff, firmware patcher
    custom_firmware/       #   Proof-of-concept ARM binary

  tools/                   # Standalone analysis scripts
```

---

## Algorithms

| Algorithm | Approach | MAE | Status |
|-----------|----------|-----|--------|
| algo1_custom | Rule-based HR zones + EPOC | ~8.3 | Done |
| algo2_sleepecg | SleepECG ML | ~8.3 | Done |
| algo3_ml | Gradient Boosting (LOO-CV) | ~1.7 | Done |
| **algo4_calibrated** | **Whoop-calibrated (DE optimized)** | **2.76** | **Done** |
| algo5_ml | HistGBT + Viterbi sleep phases | 74.7% acc | In progress |

algo4_calibrated reproduces Whoop's daily Recovery, Sleep, and Strain scores with a mean absolute error of 2.76 points (on a 0-100 scale). algo5_ml classifies per-2-minute sleep phases (awake/light/deep/REM) with 74.7% leave-one-night-out accuracy.

---

## Firmware Findings

The Whoop 5.0 runs a 1.55 MB ARM Cortex-M4F firmware on an Ambiq Apollo4 Blue Plus SoC:

- **RTOS:** QP (Quantum Platform) with 24 Active Objects and 394 signals
- **Sensors:** TDK ICM-45686 (IMU), ams AS6221 (temp), OnSemi LC709205F (fuel gauge), TI LP5562 (LED), TI DRV2625 (haptic), unknown PPG/AFE
- **Security:** CRC32-only firmware authentication (no cryptographic signatures in application firmware)

See [firmware/README.md](firmware/) for the full analysis toolkit.

---

## Legal

This project is for **educational and research purposes**. It is protected under EU Directive 2009/24/EC Article 6 and German UrhG &sect;69e, which permit reverse engineering for interoperability.

- No proprietary code is distributed
- Firmware binaries are not included (download with your own credentials)
- All personal data has been sanitized

---

## License

MIT &mdash; see [LICENSE](LICENSE)
