"""whoop dashboard — Generate HTML dashboards."""

import subprocess
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "whoop-companion" / "data" / "scripts"
ALGO_DIR = REPO_ROOT / "algorithms"


@click.command()
@click.option("--type", "dash_type", type=click.Choice(["api", "sensor", "analysis"]),
              default="api", help="Dashboard type")
def dashboard(dash_type):
    """Generate an HTML dashboard from exported data."""
    if dash_type == "api":
        script = SCRIPTS_DIR / "generate_api_dashboard.py"
        if not script.exists():
            raise click.ClickException(f"Script not found: {script}")
        click.echo("Generating API dashboard...")
        subprocess.run([sys.executable, str(script)], cwd=str(SCRIPTS_DIR), check=True)

    elif dash_type == "sensor":
        script = SCRIPTS_DIR / "generate_dashboard.py"
        if not script.exists():
            raise click.ClickException(f"Script not found: {script}")
        click.echo("Generating sensor dashboard...")
        subprocess.run([sys.executable, str(script)], cwd=str(SCRIPTS_DIR), check=True)

    elif dash_type == "analysis":
        script = ALGO_DIR / "analyze_all.py"
        if not script.exists():
            raise click.ClickException(f"Script not found: {script}")
        click.echo("Running full algorithm analysis...")
        subprocess.run([sys.executable, str(script)], cwd=str(ALGO_DIR), check=True)
