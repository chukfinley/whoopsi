"""whoop deep-dive — Pull detailed per-day data."""

import time
from pathlib import Path

import click

from whoop_cli import auth
from whoop_cli.api import api_get, paginate, save_json, RATE_LIMIT


ENDPOINTS = [
    ("sleep", "home-service/v1/deep-dive/sleep"),
    ("recovery", "home-service/v1/deep-dive/recovery"),
    ("strain", "home-service/v1/deep-dive/strain"),
    ("last_night", "home-service/v1/deep-dive/sleep/last-night"),
]


def find_activity_ids(obj):
    """Recursively find activity_id values in nested JSON."""
    ids = set()
    if isinstance(obj, dict):
        if "activity_id" in obj:
            ids.add(str(obj["activity_id"]))
        for v in obj.values():
            ids |= find_activity_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            ids |= find_activity_ids(v)
    return ids


@click.command("deep-dive")
@click.option("--date", "-d", default=None, help="Single date (YYYY-MM-DD) or 'all'")
@click.option("--output", "-o", default="whoop_backup", type=click.Path(),
              help="Output directory")
@click.option("--token", "-t", default=None, help="Bearer token (skip saved auth)")
@click.option("--force", "-f", is_flag=True, help="Re-download existing dates")
def deep_dive(date, output, token, force):
    """Pull detailed deep-dive data (sleep stages, recovery, strain) per day."""
    if token:
        access = token
    else:
        td = auth.get_token()
        access = td["access_token"]

    out = Path(output)
    deep_dir = out / "deep_dive"
    act_dir = out / "activities"
    deep_dir.mkdir(parents=True, exist_ok=True)
    act_dir.mkdir(parents=True, exist_ok=True)

    if date and date != "all":
        dates = [date]
    else:
        # Get all dates from cycles
        click.echo("Fetching cycle dates...")
        cycles = paginate("developer/v1/cycle", access)
        dates = sorted({c.get("start", "")[:10] for c in cycles if c.get("start")},
                       reverse=True)
        click.echo(f"  {len(dates)} dates available")

    all_activity_ids = set()
    fetched = 0

    for i, ds in enumerate(dates):
        target = deep_dir / f"{ds}.json"
        if target.exists() and not force:
            # Skip if already complete (>100KB usually means full data)
            if target.stat().st_size > 100_000:
                continue

        day_data = {}
        for name, ep in ENDPOINTS:
            data = api_get(ep, access, {"date": ds}, quiet=True)
            if data:
                day_data[name] = data
            time.sleep(RATE_LIMIT)

        # Also fetch home for activity IDs
        home = api_get("home-service/v1/home", access, {"date": ds}, quiet=True)
        if home:
            all_activity_ids |= find_activity_ids(home)
        time.sleep(RATE_LIMIT)

        if day_data:
            save_json(target, day_data)
            fetched += 1
            size = target.stat().st_size
            click.echo(f"  {ds}: {size:,} bytes ({len(day_data)} sections)")

    # Fetch activities
    if all_activity_ids:
        click.echo(f"\nFetching {len(all_activity_ids)} activities...")
        for aid in all_activity_ids:
            target = act_dir / f"activity_{aid}.json"
            if target.exists() and not force:
                continue
            data = api_get("core-details-bff/v1/cardio-details", access,
                           {"activityId": aid}, quiet=True)
            if data:
                save_json(target, data)
            time.sleep(RATE_LIMIT)

    # Trends (latest date)
    if dates:
        trends_dir = out / "trends"
        trends_dir.mkdir(parents=True, exist_ok=True)
        latest = dates[0]
        for t in ["sleep", "strain", "recovery"]:
            data = api_get(f"home-service/v1/deep-dive/{t}/trends", access,
                           {"date": latest}, quiet=True)
            if data:
                save_json(trends_dir / f"{t}_trends.json", data)
            time.sleep(RATE_LIMIT)

    click.echo(f"\nDone. {fetched} days fetched, {len(all_activity_ids)} activities.")
