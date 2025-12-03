# Whoop Data Export & Dashboard

Export **all** your Whoop data and visualize it in a local dashboard. No root required -- uses your regular Whoop email and password.

## Quick Start

```bash
git clone <repo-url> && cd ble-sync/data

# 1. Export your data
python3 scripts/whoop_export.py

# 2. Generate dashboard
python3 scripts/generate_api_dashboard.py

# 3. Open dashboard
open whoop_backup/dashboard.html   # or xdg-open on Linux
```

## Requirements

- Python 3.8+
- `requests` library (auto-installed if missing)

## Usage

**Interactive** (prompts for email and password):
```bash
python3 scripts/whoop_export.py
```

**With email** (prompts for password):
```bash
python3 scripts/whoop_export.py --email your@email.com
```

**With token** (if you already have a Bearer token):
```bash
python3 scripts/whoop_export.py --token YOUR_ACCESS_TOKEN
```

**Custom output directory:**
```bash
python3 scripts/whoop_export.py -o my_backup
```

## Generate Dashboard

After exporting, generate an HTML dashboard from the data:

```bash
python3 scripts/generate_api_dashboard.py
```

## What Data is Exported?

| Data | Endpoint | File |
|------|----------|------|
| User profile | `user/profile/basic` | `user_profile.json` |
| Body measurements | `user/measurement/body` | `body_measurement.json` |
| All cycles | `developer/v1/cycle` | `all_cycles.json` |
| Health overview | `health-tab-bff` | `health_tab.json` |
| Rollups (30/90/180/365d) | `rollups-service` | `rollups_*.json` |
| Daily sleep deep dive | `home-service/deep-dive/sleep` | `deep_dive/YYYY-MM-DD.json` |
| Daily recovery deep dive | `home-service/deep-dive/recovery` | per-date files |
| Daily strain deep dive | `home-service/deep-dive/strain` | per-date files |
| Sleep/strain/recovery trends | `home-service/deep-dive/*/trends` | `trends/*.json` |
| Activity details | `core-details-bff/cardio-details` | `activities/activity_*.json` |

## Directory Structure

```
whoop_backup/
  api/
    user_profile.json
    body_measurement.json
    all_cycles.json
    health_tab.json
    rollups_30d.json
    rollups_90d.json
    rollups_180d.json
    rollups_365d.json
    deep_dive_all.json
  deep_dive/
    2025-01-15.json
    2025-01-14.json
    ...
  trends/
    sleep_trends.json
    strain_trends.json
    recovery_trends.json
  activities/
    activity_12345.json
    ...
```

## Privacy

- All data is saved locally. Nothing is sent to third-party servers.
- Your password is used only to authenticate with Whoop's servers (AWS Cognito). It is never stored.
- Tokens are not saved to disk.
- The `.gitignore` excludes backup directories and HTML files to prevent accidental commits.

## License

MIT
