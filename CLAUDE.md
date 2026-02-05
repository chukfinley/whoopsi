# Whoop Reverse Engineering Project

**Status: ACTIVE** — Full Sync fixed and running. Official Whoop app has full BT access.

Open-source tools for extracting and analyzing raw sensor data from Whoop 4.0/5.0 fitness bands.
Legal basis: EU Directive 2009/24/EC Article 6 + German UrhG §69e (reverse engineering for interoperability).

---

## Documentation Map

| Document | What it covers |
|----------|---------------|
| **This file (CLAUDE.md)** | Project overview, quick start, architecture, status |
| **[agents.md](agents.md)** | Sleep phase classifier state, server setup, detailed algorithm docs |
| **[PLAN.md](PLAN.md)** | Flutter app replacement roadmap (6 phases, sprints) |
| **[ble-sync/CLAUDE.md](ble-sync/CLAUDE.md)** | Full BLE protocol documentation (AA01 format, 40+ commands) |
| **[firmware/README.md](firmware/README.md)** | Firmware RE toolkit, .zbin format, analysis pipeline |
| **[firmware/WHOOP_FIRMWARE_REPORT.md](firmware/WHOOP_FIRMWARE_REPORT.md)** | German-language firmware architecture report |
| **[algorithms/CLAUDE.md](algorithms/CLAUDE.md)** | Algorithm-specific notes |
| **[app/CLAUDE.md](app/CLAUDE.md)** | Flutter app architecture (Provider, go_router, BLE) |

---

## Project Structure

```
whoop/
  cli/               # Python CLI tool (pip install -e ./cli)
    whoop_cli/
      auth.py              # Cognito login, JWT decode, token refresh
      api.py               # Shared API client (rate limiting, pagination)
      commands/            # login, status, export, deep-dive, dashboard

  ble-sync/         # Android BLE companion app (Kotlin/Jetpack Compose)
    app/src/main/java/com/whoopcapture/
      MainActivity.kt      # Sync dashboard UI (Compose)
      WhoopBleService.kt   # BLE connection, sync loop, data extraction
      WhoopProtocol.kt     # AA01 packet builder (all BLE commands)
      WhoopDataDecoder.kt  # Sensor packet decoder (0x2F format)
      ChartScreen.kt       # HR/SpO2 charts (Compose)
      AutoSyncWorker.kt    # WorkManager periodic sync (6h)
      db/                  # Room entity + DAO
    data/scripts/          # Python export/dashboard scripts

  app/              # Flutter app (15+ screens, 12 services)
    lib/services/          # BLE, API, AI, weather, hydration, journal
    lib/screens/           # Home, sleep, recovery, stress, report, etc.
    lib/widgets/           # Score gauges, charts, cards

  algorithms/              # Python scoring & analysis
    analyze_all.py         # Full dashboard (5 algos + comparison)
    algo1_custom/          # Rule-based (HR zones + EPOC)
    algo2_sleepecg/        # SleepECG ML sleep staging
    algo3_ml/              # Gradient Boosting (LOO-CV)
    algo4_calibrated/      # Whoop-calibrated formulas (MAE 2.76)
    algo5_ml/              # ML sleep phase classifier (74.7% LONO)
    eval_lono.py           # Leave-one-night-out evaluation
    optimize_algo4.py      # Differential evolution optimizer
    common/preprocessing.py # HRV, RHR, respiratory rate

  firmware/      # Firmware reverse engineering toolkit
    analysis/              # 6 analysis tracks (A-F) + report generator
    tools/                 # zbin_builder, firmware_diff, firmware_patcher
    custom_firmware/       # Proof-of-concept ARM binary
    firmware_downloader.py # Download FW from Whoop API

  tools/                   # BLE packet analysis (parse_hci, decode_packets)
```

---

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| **ble-sync** (Kotlin BLE) | ACTIVE | Full Sync working, large data volumes confirmed |
| **cli** (Python) | DONE | login, status, export, deep-dive, dashboard |
| **algo4_calibrated** (daily scores) | DONE | MAE 2.76 (Recovery/Sleep/Strain) |
| **algo5_ml** (sleep phases) | ON HOLD | 74.7% LONO accuracy, see agents.md for details |
| **app** (Flutter) | ON HOLD | 15+ screens, needs Kotlin BLE integration |
| **Firmware RE** | DONE | 6 tracks, HTML report, tools, custom FW skeleton |
| **Publication prep** | DONE | Personal data sanitized, .gitignore hardened |

### What Works Right Now
1. Install companion app → Full Sync downloads entire circular buffer (~20 days)
2. Official Whoop app runs normally alongside (BT permissions restored)
3. Pull DB → run `analyze_all.py` → HTML dashboard with scores
4. `whoop deep-dive --date all` → downloads ground truth from cloud
5. `eval_lono.py` → evaluates sleep phase accuracy (76.2% 4-fold, 74.7% LONO)

---

## Quick Start

### 1. Build & Install Companion App
```bash
cd ble-sync
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### 2. Sync Data from Strap
- **Smart Sync** ("Sync Now"): Starts from current trim, stops after 15 all-duplicate bursts
- **Full Sync** (orange button): Disconnects/reconnects to reset trim, re-downloads entire buffer (~8-10h for 20 days)
- Auto-syncs every 6h via WorkManager
- **Note**: Official Whoop app can run alongside with full BT permissions

### 3. Pull Sensor Database
```bash
adb shell "run-as com.whoopcapture cat databases/whoop_capture.db" > whoop_capture.db
cp whoop_capture.db algorithms/data/raw/
```

### 4. Install & Use CLI
```bash
pip install -e ./cli
whoop login --email you@email.com
whoop deep-dive --date all
whoop export --output ble-sync/data/whoop_backup
```

### 5. Run Analysis
```bash
cd algorithms
python3 analyze_all.py          # Dashboard: full_dashboard.html
python3 eval_lono.py            # Sleep phase accuracy (LONO)
python3 eval_lono.py --quick    # Fast 4-fold CV
```

### 6. Firmware Analysis
```bash
cd firmware
python3 firmware_downloader.py --email you@email.com  # Download latest FW
cd analysis && python3 track_a_disassembly.py          # Run analysis tracks
python3 generate_report.py                             # HTML master report
cd ../tools && python3 zbin_builder.py --verify ../maverick_ambiq_*/maverick-*.zbin
```

---

## Algorithms

| Algorithm | Approach | MAE | Accuracy | Status |
|-----------|----------|-----|----------|--------|
| algo1_custom | Rule-based HR zones + EPOC | ~8.3 | — | Done |
| algo2_sleepecg | SleepECG ML | ~8.3 | — | Done |
| algo3_ml | Gradient Boosting (LOO-CV) | ~1.7 | — | Done |
| algo4_calibrated | Whoop-calibrated (DE optimized) | **2.76** | — | Done |
| algo5_ml | HistGBT + Viterbi (sleep phases) | — | **74.7%** LONO | On hold |

### algo5_ml Sleep Phase Classifier (detailed state in agents.md)
- 84 features per 2-min window (HR, HRV, movement, spectral, temporal, sequential)
- HistGradientBoostingClassifier + Viterbi post-processing
- Leave-one-night-out cross-validation
- Main weakness: awake recall 16.4% (awake during sleep looks like light sleep)
- Next steps: better spectral features, sequence modeling, more training data

---

## BLE Protocol (summary)

Full docs: `ble-sync/CLAUDE.md`

```
AA01 Frame: [0xAA][0x01][len:2][routing:2][CRC16:2][type:1][payload:var][CRC32:4]
```

- CRC-16/MODBUS (reflected, init=0xFFFF) protects header
- CRC32 protects payload, all payloads 4-byte aligned
- 40+ commands documented, 29 known from firmware strings
- Key: 0x16 (dump history), 0x19 (trim), 0x22 (data range), 0x2F (sensor packet)

---

## Firmware RE (summary)

Full docs: `firmware/README.md` and `WHOOP_FIRMWARE_REPORT.md`

- **Hardware**: Ambiq Apollo4 Blue Plus (Cortex-M4F, 96 MHz, BLE 5.1)
- **RTOS**: QP (Quantum Platform), 24 Active Objects, 394 signals
- **Sensors**: ICM-45686 (IMU), AS6221 (temp), LC709205F (fuel), LP5562 (LED), DRV2625 (haptic), unknown PPG/AFE
- **Security**: CRC32-only firmware auth (no crypto signatures in app FW)
- **Tools**: zbin_builder (build/verify/extract .zbin), firmware_diff, firmware_patcher
- **Custom FW**: Proof-of-concept "Hello World" ARM binary (668 bytes, compiles)
- **Open question**: Will SBL (ROM bootloader) accept unsigned firmware?

---

## Authentication

Cognito (Mobile API) — used by cli and ble-sync app:
- Auth flow: `USER_PASSWORD_AUTH` via AWS Cognito
- ClientId: extract from official Whoop APK (or set `WHOOP_COGNITO_CLIENT_ID` env var)
- Token: 24h access token, long-lived refresh token
- Stored at: `~/.whoop/token.json` (chmod 600)

OAuth2 (Developer API) — limited endpoints:
- Client ID + secret, browser-based flow
- Basic cycles, sleep, recovery data only

---

## Circular Buffer & Trim Pointer

- Strap stores ~131K positions in sector 10 (~14-20 days observed)
- Trim pointer is **per bond identity** — each paired device has its own trim state
- **NEVER** use FORCE_TRIM(0xFEFEFEFE) — marks ALL data consumed permanently for this bond
- FORCE_TRIM(0,0) does NOT reliably rewind — only exposes wrap-around segment near write head
- **Best approach for Full Sync**: Unpair and re-pair (or disconnect/reconnect) to get fresh trim state
- Official Whoop app does NOT use FORCE_TRIM during sync — only SEND_HISTORICAL_DATA + ACK loop
- Smart Sync checks coverage, only trims after successful DB insert

### Key Discovery: Per-Bond Trim State
The strap maintains separate trim pointers per bonded device identity. If FORCE_TRIM_ALL is called,
it permanently marks all data as consumed **for that specific bond**. The only way to recover is to
unpair the device in Android Bluetooth settings and re-pair, which gives a fresh bond identity with
a reset trim pointer. Data on the strap is never actually deleted — only marked as consumed per-bond.

### Official App Sync Flow (from decompiled APK)
The official Whoop app's sync is simple — no FORCE_TRIM involved:
1. GET_DATA_RANGE (0x22) — check what's available
2. SEND_HISTORICAL_DATA (0x16) — start dump (sent ONCE)
3. Receive burst of data packets on fd4b0005
4. Receive 0x31 HISTORY_END event with sector:offset
5. Send HISTORICAL_DATA_RESULT (0x17) ACK with those bytes
6. Strap automatically sends next burst (no need to re-send 0x16)
7. Repeat 3-6 until 0x31 HISTORY_COMPLETE event

---

## Publication Readiness

### Sanitization Complete
- All personal identifiers replaced with placeholders (`<USER_ID>`, `<STRAP_SERIAL>`, `<STRAP_MAC>`)
- Employee names redacted from firmware docs
- Cognito ClientId removed from documentation (kept in code via env var fallback)
- .gitignore hardened: blocks all personal data, backups, databases, HAR files, health exports

### Before Publishing (TODO)
- [ ] Responsible disclosure to Whoop Security (CRC32-only firmware auth)
- [ ] Extract whoop/ as standalone repo (`git filter-branch` or `git subtree split`)
- [ ] Clean git author/email (set to pseudonym or "Anonymous")
- [ ] Add LICENSE file (MIT or Apache-2.0)
- [ ] Add legal disclaimer to README
- [ ] Final grep for any remaining personal data


### Legal Assessment
- **Protected by**: UrhG §69e (DE), EU Directive 2009/24/EC Art. 6 (interoperability)
- **No proprietary code distributed**: Only analysis tools and findings
- **Risk**: Whoop ToS likely prohibit RE (contract breach, not criminal)
- **Realistic worst case**: DMCA takedown on GitHub (counter-notice under EU law)

---

## Files NOT to Commit (Private Data)

All handled by `.gitignore`, but listed here for reference:
- `~/.whoop/token.json` — Auth tokens
- `ble-sync/data/backup/` — Android app backup
- `ble-sync/data/whoop_backup/` — Cloud API exports
- `algorithms/data/raw/` — Sensor DB + ground truth
- `captures/` — HAR files with auth tokens
- `*.har`, `*.db`, `*.sqlite` — Raw data files
- `recovered_whoop_data.json`, `whoop_api_endpoints.json` — Personal data
- `QUICK_REFERENCE.txt`, `DATA_RECOVERY_SUMMARY.md`, `README_DATA_RECOVERY.md` — Personal data
