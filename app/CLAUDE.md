# Open Whoop Flutter App

## Build & Deploy

**IMPORTANT: Always build RELEASE APK, never debug.**

```bash
cd app
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Debug builds are extremely slow on-device (no AOT, no tree-shaking, debug overhead).
Release builds use ahead-of-time compilation and full optimization.

## Architecture

- State management: Provider + ChangeNotifier
- Routing: go_router with ShellRoute for bottom nav (3 tabs: Home, AI Coach, Settings)
- Theme: AMOLED dark (WhoopTheme in core/theme.dart)
- API: Whoop BFF via ApiService with disk cache (StorageService)
- BLE: flutter_blue_plus for Whoop strap connection

## Key Patterns

- Use `Consumer<T>` for scoped rebuilds, not `context.watch<T>()` on entire screens
- API responses are cached to disk — always show cached data first, then refresh
- All screens use GlassCard/GradientScaffold widgets for consistent look
