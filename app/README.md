# Open Whoop (Flutter)

Alternative open-source client app for the Whoop fitness band. Built with Flutter, targeting Android (and theoretically iOS/desktop).

## Status: Experimental

This app has 22 screens and 16 services but is not a fully functional Whoop replacement yet. The BLE connection and cloud data sync work in principle, but cloud data upload is unreliable. Use [ble-sync](../ble-sync/) for reliable raw data capture and [cli](../cli/) for cloud data export.

## Features

- **22 screens:** Home, sleep detail, recovery detail, strain detail, stress, journal, AI coach chat, settings, onboarding, login, trends, calendar, sensor data, analysis, report, device, health, activity detail/summary/timer, start activity, day detail
- **BLE connection** to Whoop strap via flutter_blue_plus
- **Cloud API integration** with Cognito auth, data caching, and background refresh
- **AMOLED dark theme** with glassmorphism design
- **Local scoring engine** that computes Recovery/Sleep/Strain from sensor data

## Build

```bash
cd app

# Set the Cognito Client ID (extract from Whoop APK)
# Pass it as a compile-time env var:
flutter build apk --release --dart-define=WHOOP_COGNITO_CLIENT_ID=<your-client-id>

adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Always build release, not debug (debug builds are extremely slow on-device).

## Architecture

- **State management:** Provider + ChangeNotifier
- **Routing:** go_router with ShellRoute (3 tabs: Home, AI Coach, Settings)
- **Theme:** AMOLED dark (WhoopTheme in `core/theme.dart`)
- **API:** Whoop BFF via ApiService with disk cache (StorageService)
- **BLE:** flutter_blue_plus for strap connection

### Key Services

| Service | Purpose |
|---------|---------|
| `api_service.dart` | Whoop cloud API client with rate limiting |
| `auth_service.dart` | Cognito login, token management |
| `ble_service.dart` | BLE connection to Whoop strap |
| `storage_service.dart` | Disk cache for API responses |
| `sensor_db_service.dart` | Local SQLite for sensor data |
| `sync_orchestrator.dart` | Coordinates BLE + API sync |
| `upload_service.dart` | Upload sensor data to Whoop cloud |
| `scoring_engine.dart` | Local Recovery/Sleep/Strain scoring |
| `analysis_service.dart` | Data analysis and trends |
| `ai_service.dart` | AI coaching integration |

### Key Patterns

- Use `Consumer<T>` for scoped widget rebuilds
- API responses cached to disk &mdash; show cached data first, then refresh
- All screens use GlassCard/GradientScaffold widgets for consistent look

## Project Structure

```
lib/
  main.dart
  core/
    constants.dart     # API endpoints, auth config
    router.dart        # go_router setup
    theme.dart         # AMOLED dark theme
  models/              # 12 data models
  screens/             # 22 screens
  services/            # 16 services
  widgets/             # 10 reusable widgets
```
