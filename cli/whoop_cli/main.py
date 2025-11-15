"""Whoop CLI entry point."""

import click

from whoop_cli.commands.login import login
from whoop_cli.commands.status import status
from whoop_cli.commands.export import export
from whoop_cli.commands.deep_dive import deep_dive
from whoop_cli.commands.dashboard import dashboard


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Whoop CLI — Extract and analyze Whoop 4.0/5.0 data."""


cli.add_command(login)
cli.add_command(status)
cli.add_command(export)
cli.add_command(deep_dive)
cli.add_command(dashboard)
