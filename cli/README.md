# Whoop CLI

Command-line tool for extracting and analyzing data from Whoop 4.0/5.0 fitness bands.

## Install

```bash
pip install -e .
```

## Quick Start

```bash
# Login (saves token to ~/.whoop/token.json, auto-refreshes)
whoop login --email you@email.com

# Check auth status
whoop status

# Export all data
whoop export --output ./whoop_backup

# Pull deep-dive for a specific day
whoop deep-dive --date 2025-01-15

# Pull all deep-dive data (skips already-downloaded days)
whoop deep-dive --date all

# Force re-download
whoop deep-dive --date all --force

# Generate dashboards
whoop dashboard --type api        # Whoop-style analytics
whoop dashboard --type analysis   # Algorithm comparison
```

## Commands

| Command | Description |
|---------|-------------|
| `whoop login` | Authenticate via email/password or refresh token |
| `whoop status` | Show token validity and user info |
| `whoop export` | Full data export (profile, cycles, deep-dive, trends, activities) |
| `whoop deep-dive` | Per-day detailed data (sleep stages, recovery, strain) |
| `whoop dashboard` | Generate HTML dashboards (api, sensor, analysis) |

## Authentication

Uses AWS Cognito (same as official Whoop app). Three ways to authenticate:

### 1. Email/Password Login

```bash
whoop login --email you@email.com
# Password is prompted securely
```

### 2. Refresh Token Bootstrap

If you have a Cognito refresh token (e.g. from a HAR file capture), you can bootstrap without a password:

```bash
whoop login --refresh-token eyJjdHki...
```

**How to get a refresh token:**
1. Open the Whoop app while running an HTTP proxy (e.g. HTTPToolkit, mitmproxy)
2. Find the `Authorization: Bearer eyJ...` header in requests to `api.prod.whoop.com`
3. Look for the `RefreshToken` in the Cognito `InitiateAuth` response
4. Pass it to `whoop login --refresh-token <token>`

### 3. Auto-Refresh (No Arguments)

Once logged in, the tool automatically refreshes expired tokens. Just run commands:

```bash
whoop status    # auto-refreshes if needed
whoop export    # auto-refreshes if needed
```

To manually force a refresh:

```bash
whoop login     # refreshes saved token, no email needed
```

### Token Storage

Tokens are saved to `~/.whoop/token.json` with chmod 600 (owner-only readable):

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJj...",
  "user_id": "<USER_ID>",
  "email": "you@email.com",
  "expires_at": 1770492279
}
```

- Access tokens expire after **24 hours** and are auto-refreshed
- Refresh tokens are **long-lived** (months) and survive across sessions
- All commands auto-refresh expired tokens transparently

You can also bypass saved auth: `whoop export --token eyJ...`

## Data Output

```
whoop_backup/
  api/                    # User profile, cycles, rollups, health tab
  deep_dive/              # Per-day JSON (sleep, recovery, strain, sleep stages)
  trends/                 # Sleep/strain/recovery trends
  activities/             # Individual activity details
```
