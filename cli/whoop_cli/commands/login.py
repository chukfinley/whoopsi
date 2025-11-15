"""whoop login — Authenticate and save token."""

import click

from whoop_cli import auth


@click.command()
@click.option("--email", "-e", default=None, help="Whoop account email")
@click.option("--password", "-p", default=None, help="Password (prompted if --email given)")
@click.option("--refresh-token", "-r", default=None, help="Bootstrap from a Cognito refresh token")
def login(email, password, refresh_token):
    """Log in to Whoop and save credentials locally.

    Three ways to authenticate:

    \b
    1. Email/password:     whoop login --email you@email.com
    2. Refresh token:      whoop login --refresh-token eyJj...
    3. Auto-refresh:       whoop login   (uses saved token)
    """
    try:
        if refresh_token:
            # Bootstrap from refresh token (e.g. extracted from HAR file)
            td = auth.refresh({"refresh_token": refresh_token})
            click.echo(f"Authenticated via refresh token (user_id: {td['user_id']})")
            click.echo(f"Token saved to {auth.TOKEN_FILE}")

        elif email:
            if not password:
                password = click.prompt("Password", hide_input=True)
            td = auth.login(email, password)
            click.echo(f"Logged in as {email} (user_id: {td['user_id']})")
            click.echo(f"Token saved to {auth.TOKEN_FILE}")

        else:
            # Try auto-refresh from saved token
            td = auth.load_token()
            if not td or not td.get("refresh_token"):
                raise click.ClickException(
                    "No saved token found. Use:\n"
                    "  whoop login --email you@email.com\n"
                    "  whoop login --refresh-token eyJj..."
                )
            td = auth.refresh(td)
            click.echo(f"Token refreshed (user_id: {td['user_id']})")
            click.echo(f"Token saved to {auth.TOKEN_FILE}")

    except RuntimeError as e:
        raise click.ClickException(str(e))
