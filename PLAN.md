# Whoop Replacement App — Master Plan

*Last updated: 2026*

## Goal

Build a complete open-source Whoop replacement app in **Flutter** (with Kotlin for BLE) that:
- Replaces the official Whoop app entirely
- Syncs raw sensor data via BLE (existing Kotlin code)
- Computes all scores locally (recovery, sleep, strain, HRV, RHR, SpO2, etc.)
- Works fully offline-first (never deletes data, never auto-logs out)
- Optionally syncs to our own cloud backend
- Exports data in any format
- Includes our custom reverse-engineered scoring algorithms

---

## Phase 1: BLE Sync + Local DB (Kotlin Platform Channel)

**Status: DONE** (existing Kotlin companion app)

### What Exists
- `WhoopBleService.kt` (976 lines) — Full BLE connection, sync loop, battery
- `WhoopProtocol.kt` (342 lines) — AA01 packet builder, CRC16/CRC32, 40+ commands
- `WhoopDataDecoder.kt` (211 lines) — 0x2F sensor packet parser
- `SensorRecordDao.kt` (72 lines) — Room DAO with all queries
- `AutoSyncWorker.kt` (307 lines) — WorkManager 6h background sync

### Key Code Locations
| Feature | File | Lines | Notes |
|---------|------|-------|-------|
| BLE connect/disconnect | `WhoopBleService.kt` | 111-162 | `connect()`, `disconnect()` |
| Smart sync (coverage-based) | `WhoopBleService.kt` | 427-522 | Checks coverage %, no-rewind on gap fill |
| Full sync (rewind all) | `WhoopBleService.kt` | 524-545 | FORCE_TRIM(0,0) + full dump |
| Sync loop | `WhoopBleService.kt` | 547-661 | Burst-wait-repeat, disconnect resume |
| Battery parsing | `WhoopBleService.kt` | 931-953 | Heuristic: first byte in 5..100 range |
| AA01 framing | `WhoopProtocol.kt` | 91-150 | CRC16 header + CRC32 payload + 4-byte align |
| 0x2F decoder | `WhoopDataDecoder.kt` | 30-170 | HR from RR, SpO2, accel, gyro, timestamp |
| DB schema | `db/SensorRecord.kt` | 1-29 | timestamp UNIQUE, HR, RR, SpO2, accel, gyro |
| Background sync | `AutoSyncWorker.kt` | 1-307 | WorkManager periodic 6h |

### What's Been Fixed
- [x] Battery: heuristic scan for 5..100 (was reading version byte as %)
- [x] Coverage check: today's records / expected seconds → decides sync strategy
- [x] No-rewind gap filling: strap remembers trim across BLE sessions
- [x] `syncInterrupted` flag: detects disconnect during sync, resumes from last position
- [x] maxRounds: 200 → 400 to cover entire circular buffer

### To Port to Flutter
- [ ] Create Kotlin platform channel for BLE operations
- [ ] Expose: connect, disconnect, requestSmartSync, requestFullSync, queryBattery
- [ ] Stream sync status to Flutter via EventChannel
- [ ] Keep Kotlin Room DB or migrate to drift/sqflite

---

## Phase 2: Flutter App Shell + Offline-First Architecture

### Existing Flutter Code
The `app/` directory has a mature Flutter app with:
- `lib/services/ble_service.dart` (572 lines) — Full BLE with AA01 protocol
- `lib/services/api_service.dart` (229 lines) — Whoop Cloud API client
- `lib/services/sensor_db_service.dart` (107 lines) — Local sensor DB
- `lib/screens/home_screen.dart` (885 lines) — Dashboard with score gauges
- `lib/screens/sleep_detail_screen.dart` (954 lines) — Sleep stages, trends
- `lib/screens/activity_detail_screen.dart` (809 lines) — HR zones, calories
- `lib/screens/recovery_screen.dart` — Recovery metrics
- `lib/screens/trends_screen.dart` — Multi-day trends
- `lib/screens/settings_screen.dart` — App settings
- `lib/widgets/` — score_gauge, activity_rings, hr_chart, sleep_bar, calendar_heatmap

### Architecture Decisions
- [ ] **Offline-first**: All data stored locally, never deleted
- [ ] **Auth**: User stays logged in forever (no auto-logout)
- [ ] **Sync**: Background only, never blocking UI
- [ ] **Data**: Never requires internet to view historical data
- [ ] **Export**: CSV, JSON, SQLite DB export at any time

### Key Models (from app)
```
lib/models/
├── cycle.dart         # Day cycle (strain, recovery, sleep summary)
├── sleep.dart         # Sleep stages, duration, efficiency
├── recovery.dart      # HRV, RHR, respiratory rate, recovery %
├── sensor_record.dart # Raw sensor: HR, RR, SpO2, accel, gyro
├── activity.dart      # Workout: duration, zones, calories
├── user.dart          # User profile
```

---

## Phase 3: Local Scoring Algorithms (Port Python → Dart)

### What the Algorithms Compute

| Metric | Source | Algorithm File | Key Function |
|--------|--------|----------------|--------------|
| **Recovery %** | HRV + RHR + Sleep + Resp | `algo4_calibrated/engine.py` | `compute_recovery()` |
| **Sleep Score** | Hours, consistency, efficiency, stress | `algo4_calibrated/engine.py` | `compute_sleep_score()` |
| **Strain (0-21)** | HR zones + EPOC model | `algo4_calibrated/engine.py` | `compute_whoop_strain()` |
| **HRV (RMSSD)** | RR intervals during SWS | `common/preprocessing.py` | `compute_hrv_rmssd()` |
| **RHR** | Lowest 5-min avg during sleep | `common/preprocessing.py` | `compute_rhr()` (P25+median)/2 |
| **Respiratory Rate** | RR interval variance | `algo4_calibrated/engine.py` | `compute_respiratory_rate()` |
| **Sleep Phases** | HR variability + movement | `algo4_calibrated/engine.py` | `classify_sleep_phases()` |
| **HR Zones** | Time in zones 1-5 | `common/preprocessing.py` | `compute_hr_zones()` |
| **Calories** | HR-based EPOC estimation | (to build) | Zone weights × time |
| **Steps** | Accelerometer pattern matching | (to build) | Peak detection on accel Z |
| **SpO2** | Direct from sensor | DB field | `spo2Percent` |

### Optimization Results
- **Before**: MAE 9.19
- **After**: MAE 2.76 (35K iterations differential evolution)
- **Recovery**: Hits some days exactly, others within 2-10 points
- **Sleep**: Within 1-3 points consistently
- **Strain**: Accurate for rest days, underestimates high-activity days

### Porting Strategy
1. Port `algo4_calibrated/engine.py` to Dart (most accurate)
2. Port `common/preprocessing.py` (HRV, RHR, sleep features)
3. Keep Python optimizer for offline re-training
4. App computes scores locally from sensor data

---

## Phase 4: Whoop Cloud API Integration

### Authentication (reverse-engineered)
```
Endpoint: https://api.prod.whoop.com/auth-service/v3/whoop/
Client ID: <COGNITO_CLIENT_ID> (no secret needed)
Auth Flow: USER_PASSWORD_AUTH (Cognito)
Token: 24h access token, long-lived refresh token
```

### Available Endpoints
| Endpoint | Data | Size |
|----------|------|------|
| `home-service/v1/home?date=YYYY-MM-DD` | Full dashboard | ~78KB |
| `home-service/v1/deep-dive/sleep/last-night?date=` | Sleep stages (5-min) | ~921KB |
| `home-service/v1/deep-dive/recovery?date=` | Recovery details | ~5KB |
| `home-service/v1/deep-dive/strain?date=` | Strain/HR zones | ~5KB |
| `developer/v1/cycle?limit=25` | All cycles (paginated) | ~50KB |
| `developer/v1/user/profile/basic` | User profile | ~5KB |

### Required Headers
```
Authorization: Bearer {access_token}
x-whoop-app-version: 5.430.0
x-whoop-device-platform: ANDROID
x-whoop-strap-id: 5<STRAP_SERIAL>
x-whoop-time-zone: {timezone}
```

### Cloud Upload (To Reverse-Engineer)
- [ ] Capture official app's upload traffic (HCI snoop or MITM proxy)
- [ ] Identify POST/PUT endpoints for sending sensor data
- [ ] Implement upload from our app
- **Alternative**: Build own backend (Supabase/Firebase) for personal cloud sync

---

## Phase 5: Web Dashboard (Python)

### Current State
- `algorithms/full_dashboard.html` — Interactive multi-day comparison
- Generated by `analyze_all.py`
- Shows: HR graph, recovery/sleep/strain per day, algorithm comparison

### Enhancements Needed
- [ ] Activity detection (walking, running, cycling) from accel patterns
- [ ] Calorie estimation from HR zones + BMR
- [ ] Step counting from accelerometer peaks
- [ ] "Whoop Age" computation (if possible from available data)
- [ ] SpO2 trends
- [ ] Multi-week/month trend views
- [ ] Export to PDF/image

---

## Phase 6: Flutter App — Feature Parity with Whoop

### Screens to Build

| Screen | Data Source | Whoop Feature | Priority |
|--------|------------|---------------|----------|
| **Home Dashboard** | Local DB + algo | Recovery %, Strain, Sleep score | P0 |
| **Sleep Detail** | Local DB + algo | Sleep stages, duration, efficiency | P0 |
| **Recovery Detail** | Local DB + algo | HRV, RHR, resp rate, contributors | P0 |
| **Strain Detail** | Local DB + algo | HR zones, calories, activities | P0 |
| **Live HR** | BLE real-time | Current HR, SpO2, battery | P0 |
| **Trends** | Local DB history | 7/30/90 day trends | P1 |
| **Activity Log** | Auto-detect + manual | Workouts, duration, strain | P1 |
| **Settings** | Local prefs | Sync interval, export, dark mode | P1 |
| **Health Tab** | Computed | Monthly averages, personal bests | P2 |
| **Whoop Age** | Computed | Biological age estimate | P3 |

### Offline-First Rules
1. **NEVER** auto-logout the user
2. **NEVER** delete local data, even if cloud sync fails
3. **ALWAYS** compute scores locally (don't depend on cloud)
4. **ALWAYS** allow CSV/JSON/DB export
5. Background sync is silent — user sees data immediately from local DB
6. If offline, show cached data with "last synced" timestamp
7. User can configure: sync interval (1h, 6h, 12h, manual only)
8. User can configure: how many days back to sync (1, 7, 14, all)

---

## File Structure (Proposed)

```
whoop/
  app/                          # NEW: Main Flutter app
    android/
      app/src/main/kotlin/
        com/whoop/
          ble/
            WhoopBleService.kt       # FROM: ble-sync (976 lines)
            WhoopProtocol.kt         # FROM: ble-sync (342 lines)
            WhoopDataDecoder.kt      # FROM: ble-sync (211 lines)
          platform/
            BlePlatformChannel.kt    # NEW: Flutter ↔ Kotlin bridge
    lib/
      core/
        router.dart
        theme.dart
        constants.dart
      models/
        sensor_record.dart
        cycle.dart
        sleep.dart
        recovery.dart
        activity.dart
      services/
        ble_channel.dart             # NEW: Platform channel to Kotlin BLE
        sensor_db.dart               # Local sensor DB
        scoring_engine.dart          # NEW: Port of algo4_calibrated/engine.py
        preprocessing.dart           # NEW: Port of common/preprocessing.py
        cloud_api.dart               # Whoop Cloud API client
        sync_service.dart            # NEW: Background sync orchestrator
        export_service.dart          # CSV/JSON export
      screens/
        home_screen.dart             # Dashboard (885 lines)
        sleep_detail_screen.dart     # Sleep stages (954 lines)
        recovery_screen.dart         # Recovery metrics
        strain_screen.dart           # Strain detail
        live_hr_screen.dart          # NEW: Real-time HR monitor
        trends_screen.dart           # Multi-day trends
        settings_screen.dart         # App settings
      widgets/
        score_gauge.dart
        activity_rings.dart
        hr_chart.dart
        sleep_bar.dart
        metric_card.dart

  algorithms/                    # KEEP: Python analysis + optimization
  tools/                         # KEEP: BLE packet analysis
  firmware/                      # KEEP: Firmware analysis
```

---

## Implementation Order

### Sprint 1: Foundation (1-2 days)
- [ ] Create new Flutter project at `whoop/app/`
- [ ] Copy Kotlin BLE files from `ble-sync/`
- [ ] Create platform channel (MethodChannel + EventChannel)
- [ ] Verify BLE sync works from Flutter
- [ ] Set up local DB (drift or sqflite)
- [ ] Verify sensor records stored and queryable

### Sprint 2: Scoring Engine (1-2 days)
- [ ] Port `algo4_calibrated/engine.py` to Dart
- [ ] Port `common/preprocessing.py` (HRV, RHR, sleep features)
- [ ] Verify scores match Python output
- [ ] Add scoring to DB (computed daily scores table)

### Sprint 3: Core UI (2-3 days)
- [ ] Adapt `home_screen.dart`
- [ ] Adapt `sleep_detail_screen.dart`
- [ ] Build recovery detail screen
- [ ] Build strain detail screen
- [ ] Wire up local DB → scoring → UI

### Sprint 4: Real-time + Background (1-2 days)
- [ ] Live HR screen (BLE real-time streaming)
- [ ] Background sync service (WorkManager via platform channel)
- [ ] Notification when sync completes
- [ ] Offline-first data persistence

### Sprint 5: Cloud API (1-2 days)
- [ ] Port Cognito auth
- [ ] Fetch ground truth data for comparison
- [ ] Optional cloud backup of sensor data
- [ ] Settings: sync interval, days back, auto/manual

### Sprint 6: Polish (1-2 days)
- [ ] Export (CSV, JSON, SQLite)
- [ ] Trends screen (7/30/90 days)
- [ ] Activity detection from accelerometer
- [ ] Step counting
- [ ] Calorie estimation

---

## Reference: Existing Code Cross-Reference

### BLE Protocol (fully reverse-engineered)
- **Protocol docs**: `ble-sync/CLAUDE.md` (comprehensive, 500+ lines)
- **Kotlin implementation**: `ble-sync/.../WhoopProtocol.kt`
- **Flutter implementation**: `app/lib/services/ble_service.dart`
- **Command codes**: 40+ documented in CLAUDE.md
- **Sensor packet format**: 0x2F (124-byte), fully decoded

### Scoring Algorithms (calibrated + optimized)
- **Production algorithm**: `algorithms/algo4_calibrated/engine.py`
- **Recovery formula**: Sigmoid on HRV ratio, weighted with RHR + sleep
- **Sleep scoring**: Hours, consistency, efficiency, stress (4 components)
- **Strain**: Whoop HR zones + log-scale EPOC model
- **Optimizer**: `algorithms/optimize_algo4.py` (differential evolution, 35K iter)
- **Current MAE**: 2.76

### Cloud API (reverse-engineered)
- **Auth**: Cognito `USER_PASSWORD_AUTH`, ClientId `<COGNITO_CLIENT_ID>`
- **Endpoints**: 15+ documented in `ble-sync/CLAUDE.md`
- **Export script**: `ble-sync/data/scripts/whoop_export.py`
- **Flutter API client**: `app/lib/services/api_service.dart`

### Firmware (analyzed)
- **Report**: `firmware/WHOOP_FIRMWARE_REPORT.md`
- **Chip**: Ambiq Apollo4 Blue Plus (ARM Cortex-M4F)
- **RTOS**: QP with 24 Active Objects
- **Sensors**: PPG (HR/SpO2), IMU (ICM-45686), temp (AS6221), fuel gauge (LC709205F)
- **Storage**: Circular buffer, sector 10, ~131K positions, ~7-14 days

---

## Known Limitations

1. **Movement data**: `rawHex` field is NULL in DB → accel/gyro not stored. Fix: store rawHex in WhoopDataDecoder.kt
2. **Sleep staging**: Without movement, classification uses HR only → awake detection limited
3. **Strain**: Underestimates high-activity days (log-scale compression)
4. **Step counting**: Not implemented yet (need accelerometer pattern matching)
5. **Calorie estimation**: Not implemented yet (need HR-based EPOC model)
6. **Upload to Whoop Cloud**: Not yet reverse-engineered (only read endpoints known)
7. **BLE disconnects**: Strap drops after ~2-3 min of continuous dump (firmware limitation)
