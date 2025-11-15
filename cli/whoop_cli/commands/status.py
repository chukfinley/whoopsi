"""whoop status — Show token info and data stats."""

import time
from datetime import datetime, timezone

import click

from whoop_cli import auth


@click.command()
def status():
    """Show current auth status and token info."""
    td = auth.load_token()
    if not td:
        click.echo("Not logged in. Run: whoop login")
        return

    email = td.get("email", "?")
    user_id = td.get("user_id", "?")
    exp = td.get("expires_at", 0)
    remaining = exp - time.time()

    click.echo(f"Email:    {email}")
    click.echo(f"User ID:  {user_id}")

    if remaining > 0:
        hours = int(remaining // 3600)
        mins = int((remaining % 3600) // 60)
        exp_str = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        click.echo(f"Token:    valid ({hours}h {mins}m remaining, expires {exp_str})")
    else:
        click.echo("Token:    expired")
        if td.get("refresh_token"):
            click.echo("          Refresh token available — will auto-refresh on next API call")
        else:
            click.echo("          Run: whoop login")
