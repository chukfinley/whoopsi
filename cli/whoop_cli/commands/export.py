"""whoop export — Full Whoop data export."""

import time
from pathlib import Path

import click

from whoop_cli import auth
from whoop_cli.api import api_get, paginate, save_json, RATE_LIMIT


def progress(step: int, total: int, label: str):
    bar_len = 30
    filled = int(bar_len * step / total)
    bar = "#" * filled + "-" * (bar_len - filled)
    click.echo(f"\n[{bar}] Step {step}/{total}: {label}")


@click.command()
@click.option("--output", "-o", default="whoop_backup", type=click.Path(),
              help="Output directory")
@click.option("--token", "-t", default=None, help="Bearer token (skip saved auth)")
def export(output, token):
    """Export all Whoop data (profile, cycles, deep-dive, trends, activities)."""
    if token:
        access = token
        user_id = auth.resolve_user_id(token)
    else:
        td = auth.get_token()
        access = td["access_token"]
        user_id = td.get("user_id", "")

    out = Path(output)
    api_dir = out / "api"
    deep_dir = out / "deep_dive"
    trends_dir = out / "trends"
    act_dir = out / "activities"
    for d in [api_dir, deep_dir, trends_dir, act_dir]:
        d.mkdir(parents=True, exist_ok=True)

    total_steps = 8

    def track(path, data):
        if data is not None:
            size = save_json(path, data)
            click.echo(f"    Saved: {path.relative_to(out)} ({size:,} bytes)")

    # 1. User Profile
    progress(1, total_steps, "User Profile")
    track(api_dir / "user_profile.json",
          api_get("developer/v1/user/profile/basic", access))
    time.sleep(RATE_LIMIT)
    track(api_dir / "body_measurement.json",
          api_get("developer/v1/user/measurement/body", access))
    time.sleep(RATE_LIMIT)

    # 2. All Cycles
    progress(2, total_steps, "All Cycles (paginated)")
    cycles = paginate("developer/v1/cycle", access)
    if cycles:
        track(api_dir / "all_cycles.json", cycles)
        click.echo(f"    {len(cycles)} cycles")

    # 3. Health Tab
    progress(3, total_steps, "Health Tab")
    track(api_dir / "health_tab.json",
          api_get("health-tab-bff/v1/health-tab", access))
    time.sleep(RATE_LIMIT)

    # 4. Rollups
    progress(4, total_steps, "Rollups")
    if user_id:
        for days in [365, 180, 90, 30]:
            data = api_get(f"rollups-service/v1/rollups/{user_id}", access, {"days": days})
            if data:
                track(api_dir / f"rollups_{days}d.json", data)
            time.sleep(RATE_LIMIT)

    # 5. Deep Dive
    progress(5, total_steps, "Deep Dive (per day)")
    dates = sorted({c.get("start", "")[:10] for c in cycles if c.get("start")}, reverse=True)
    click.echo(f"    {len(dates)} dates...")

    endpoints = [
        ("sleep", "home-service/v1/deep-dive/sleep"),
        ("recovery", "home-service/v1/deep-dive/recovery"),
        ("strain", "home-service/v1/deep-dive/strain"),
        ("last_night", "home-service/v1/deep-dive/sleep/last-night"),
    ]

    all_deep = {}
    for i, ds in enumerate(dates):
        day_data = {}
        for name, ep in endpoints:
            data = api_get(ep, access, {"date": ds}, quiet=True)
            if data:
                day_data[name] = data
            time.sleep(RATE_LIMIT)
        if day_data:
            all_deep[ds] = day_data
            save_json(deep_dir / f"{ds}.json", day_data)
        if (i + 1) % 10 == 0 or i == len(dates) - 1:
            click.echo(f"    {i + 1}/{len(dates)} dates")

    if all_deep:
        track(api_dir / "deep_dive_all.json", all_deep)

    # 6. Trends
    progress(6, total_steps, "Trends")
    latest = dates[0] if dates else ""
    if latest:
        for t in ["sleep", "strain", "recovery"]:
            data = api_get(f"home-service/v1/deep-dive/{t}/trends", access,
                           {"date": latest}, quiet=True)
            if data:
                track(trends_dir / f"{t}_trends.json", data)
            time.sleep(RATE_LIMIT)

    # 7. Activities
    progress(7, total_steps, "Activity Details")
    activity_ids = set()
    for dd in all_deep.values():
        strain = dd.get("strain", {})
        for key in ["activities", "cardio_activities"]:
            for act in strain.get(key, []):
                aid = act.get("activity_id") or act.get("id")
                if aid:
                    activity_ids.add(str(aid))

    click.echo(f"    {len(activity_ids)} activities")
    for aid in activity_ids:
        data = api_get("core-details-bff/v1/cardio-details", access,
                       {"activityId": aid}, quiet=True)
        if data:
            save_json(act_dir / f"activity_{aid}.json", data)
        time.sleep(RATE_LIMIT)

    # 8. Summary
    progress(8, total_steps, "Done")
    total_files = sum(1 for _ in out.rglob("*.json"))
    total_bytes = sum(f.stat().st_size for f in out.rglob("*.json"))
    click.echo(f"\n  Location:   {out.resolve()}")
    click.echo(f"  Files:      {total_files}")
    click.echo(f"  Size:       {total_bytes / 1024 / 1024:.1f} MB")
    click.echo(f"  Cycles:     {len(cycles)}")
    click.echo(f"  Days:       {len(dates)}")
    click.echo(f"  Activities: {len(activity_ids)}")
