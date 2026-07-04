"""Typer CLI application for Deliverect Order Sync.

Commands:
  login          — Manual browser login
  calibrate      — Discover UI selectors
  run            — Full export-import-Excel workflow
  export         — Browser export + CSV download only
  import-file    — Import a manually downloaded CSV
  status         — Show current status
  reauthenticate — Force re-login
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from deliverect_sync.models import SyncRun

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deliverect_sync import __version__
from deliverect_sync.config import AppSettings
from deliverect_sync.exceptions import (
    ConcurrentRunError,
    DeliverectSyncError,
)
from deliverect_sync.logging_config import setup_logging, get_logger
from deliverect_sync.models import RunStatus

app = typer.Typer(
    name="deliverect-sync",
    help="Deliverect Order Sync — Export orders via Deliverect's official interface.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger("cli")


def _load_settings(config_path: str | None) -> AppSettings:
    """Load application settings."""
    try:
        return AppSettings.load(config_path)
    except FileNotFoundError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Invalid configuration:[/red] {e}")
        raise typer.Exit(1)


def _generate_run_id() -> str:
    """Generate a unique run ID."""
    return str(uuid.uuid4())


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
) -> None:
    """Deliverect Order Sync CLI."""
    if version:
        console.print(f"deliverect-order-sync {__version__}")
        raise typer.Exit()


@app.command()
def login(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Launch a browser for manual Deliverect login.

    Opens a visible Chromium browser and navigates to the configured
    Deliverect portal. Log in manually using your email/password or
    Google login. The program will save your session securely after
    authentication completes.

    Your password is NEVER collected, logged, or stored by this program.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    setup_logging(settings.diagnostics.log_level, settings.logs_dir, run_id)

    console.print(Panel(
        "[bold]Deliverect Login[/bold]\n\n"
        "A browser window will open. Log in to Deliverect manually.\n"
        "Your password is never collected or stored by this application.\n\n"
        f"Portal: [cyan]{settings.portal.base_url}[/cyan]",
        title="🔐 Authentication",
        border_style="green",
    ))

    try:
        from deliverect_sync.browser.session_manager import SessionManager

        manager = SessionManager(settings)
        manager.interactive_login()

        console.print("\n[green]✓ Authentication successful![/green]")
        console.print("Session saved securely. You can now run other commands.")

    except DeliverectSyncError as e:
        console.print(f"\n[red]✗ {e}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Login canceled by user.[/yellow]")
        raise typer.Exit(0)


@app.command()
def calibrate(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Discover and validate UI selectors for your Deliverect account.

    Opens a browser with your saved session and guides you through
    identifying the correct UI elements. Run this after first login
    and whenever Deliverect updates its interface.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    setup_logging(settings.diagnostics.log_level, settings.logs_dir, run_id)

    console.print(Panel(
        "[bold]Selector Calibration[/bold]\n\n"
        "This wizard will guide you through identifying UI elements\n"
        "in your Deliverect account. Follow the prompts.",
        title="🔧 Calibration",
        border_style="blue",
    ))

    try:
        from deliverect_sync.browser.calibration import CalibrationWizard

        wizard = CalibrationWizard(settings)
        wizard.run()

        console.print("\n[green]✓ Calibration complete![/green]")
        console.print("Selectors saved to selectors.yaml.")

    except DeliverectSyncError as e:
        console.print(f"\n[red]✗ {e}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Calibration canceled.[/yellow]")
        raise typer.Exit(0)


@app.command()
def run(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without making changes"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging"),
) -> None:
    """Execute the complete export-import-Excel workflow.

    Authenticates, opens Orders, applies filters, requests an export,
    waits for completion, downloads the CSV, imports into SQLite,
    and generates an Excel workbook.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    log_level = "DEBUG" if verbose else settings.diagnostics.log_level
    setup_logging(log_level, settings.logs_dir, run_id)

    logger.info("Starting sync run %s", run_id)

    try:
        from deliverect_sync.storage.database import DatabaseManager
        from deliverect_sync.workflow.export_workflow import ExportWorkflow

        db = DatabaseManager(settings.db_path)
        db.initialize()

        # Check for concurrent runs
        if db.is_run_active():
            raise ConcurrentRunError()

        workflow = ExportWorkflow(settings, db, run_id, dry_run=dry_run)

        with console.status("[bold green]Running export workflow..."):
            result = workflow.execute()

        # Display results
        _display_run_result(result, run_id)

    except ConcurrentRunError as e:
        console.print(f"\n[red]✗ {e}[/red]")
        raise typer.Exit(1)
    except DeliverectSyncError as e:
        console.print(f"\n[red]✗ [{e.error_code}] {e}[/red]")
        logger.error("Run failed: [%s] %s", e.error_code, e)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Run canceled by user.[/yellow]")
        raise typer.Exit(0)


@app.command()
def export(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Perform only the browser export and CSV download.

    Does not import into SQLite or generate Excel output.
    Useful for manual review of exported data before importing.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    setup_logging(settings.diagnostics.log_level, settings.logs_dir, run_id)

    try:
        from deliverect_sync.workflow.export_workflow import ExportWorkflow
        from deliverect_sync.storage.database import DatabaseManager

        db = DatabaseManager(settings.db_path)
        db.initialize()

        workflow = ExportWorkflow(settings, db, run_id, export_only=True)

        with console.status("[bold green]Exporting orders..."):
            result = workflow.execute()

        if result.downloaded_filename:
            console.print(f"\n[green]✓ CSV downloaded:[/green] {result.downloaded_filename}")
        else:
            console.print(f"\n[yellow]Export completed with status: {result.result}[/yellow]")

    except DeliverectSyncError as e:
        console.print(f"\n[red]✗ [{e.error_code}] {e}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Export canceled.[/yellow]")
        raise typer.Exit(0)


@app.command("import-file")
def import_file(
    filepath: str = typer.Argument(help="Path to a Deliverect order CSV file"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Import a manually downloaded Deliverect CSV file.

    Use this when browser automation is unavailable or when you
    prefer to download the export manually from Deliverect.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    setup_logging(settings.diagnostics.log_level, settings.logs_dir, run_id)

    csv_path = Path(filepath)
    if not csv_path.exists():
        console.print(f"[red]File not found:[/red] {filepath}")
        raise typer.Exit(1)

    if not csv_path.suffix.lower() == ".csv":
        console.print("[yellow]Warning:[/yellow] File does not have .csv extension.")

    try:
        from deliverect_sync.storage.database import DatabaseManager
        from deliverect_sync.importers.order_importer import OrderImporter
        from deliverect_sync.exporters.excel_exporter import ExcelExporter

        db = DatabaseManager(settings.db_path)
        db.initialize()

        # Import CSV
        importer = OrderImporter(settings, db)
        result = importer.import_csv(csv_path, run_id)

        console.print(f"\n[green]✓ Imported {result.imported_rows} rows[/green]")
        console.print(f"  New orders: {result.new_orders}")
        console.print(f"  Updated orders: {result.updated_orders}")
        console.print(f"  Rejected rows: {result.rejected_rows}")

        # Generate Excel
        exporter = ExcelExporter(settings, db)
        output_path = exporter.export(run_id)
        console.print(f"\n[green]✓ Excel output:[/green] {output_path}")

    except DeliverectSyncError as e:
        console.print(f"\n[red]✗ [{e.error_code}] {e}[/red]")
        raise typer.Exit(1)


@app.command()
def status(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Show current authentication and sync status."""
    settings = _load_settings(config)

    from deliverect_sync.security.auth_state import AuthStateManager
    from deliverect_sync.storage.database import DatabaseManager

    auth_mgr = AuthStateManager(settings.auth_dir)

    table = Table(title="Deliverect Sync Status", show_header=False, border_style="blue")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    # Authentication
    if auth_mgr.has_state():
        table.add_row("Authentication", "[green]Saved session found[/green]")
    else:
        table.add_row("Authentication", "[red]No saved session[/red]")

    # Portal
    table.add_row("Portal", settings.portal.base_url)
    table.add_row("Portal Type", settings.portal.type.value)

    # Database
    db_exists = settings.db_path.exists()
    table.add_row("Database", "[green]Exists[/green]" if db_exists else "[yellow]Not created[/yellow]")

    if db_exists:
        try:
            db = DatabaseManager(settings.db_path)
            db.initialize()

            last_run = db.get_last_run()
            if last_run:
                table.add_row("Last Run", last_run.started_at.strftime("%Y-%m-%d %H:%M:%S"))
                table.add_row("Last Result", _colorize_status(last_run.result))
                table.add_row("Last Orders", str(last_run.imported_rows))
            else:
                table.add_row("Last Run", "[dim]None[/dim]")

            active = db.is_run_active()
            table.add_row(
                "Active Run",
                "[yellow]Yes — a run is in progress[/yellow]" if active else "[dim]No[/dim]",
            )
        except Exception:
            table.add_row("Database Status", "[red]Error reading database[/red]")

    # Output
    table.add_row("Output Directory", str(settings.output.resolved_directory))

    console.print(table)


@app.command()
def reauthenticate(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Force a new login, replacing the saved session.

    Use this when your session has expired or when you need
    to log in with a different account.
    """
    settings = _load_settings(config)
    run_id = _generate_run_id()
    setup_logging(settings.diagnostics.log_level, settings.logs_dir, run_id)

    from deliverect_sync.security.auth_state import AuthStateManager

    auth_mgr = AuthStateManager(settings.auth_dir)

    if auth_mgr.has_state():
        console.print("[yellow]Deleting existing session...[/yellow]")
        auth_mgr.delete_state()

    # Delegate to login
    login(config=config)


@app.command("reset-database")
def reset_database(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    force: bool = typer.Option(False, "--force", help="Do not prompt for confirmation"),
) -> None:
    """Reset the database, creating a safe checkpoint backup.
    
    This is the safe way to clear data and start fresh since it handles
    SQLite WAL mode correctly and guarantees no locking issues.
    """
    settings = _load_settings(config)
    
    if not force:
        confirm = typer.confirm("Are you sure you want to reset the database? A backup will be created.")
        if not confirm:
            console.print("[yellow]Database reset aborted.[/yellow]")
            raise typer.Exit(0)
            
    try:
        from deliverect_sync.storage.database import DatabaseManager
        db = DatabaseManager(settings.db_path)
        
        backup_dir = settings.data_dir / "backups"
        backup_path = db.reset_database(backup_dir)
        
        if backup_path:
            console.print(f"[green]✓ Database reset successfully.[/green]")
            console.print(f"Backup saved to: {backup_path}")
        else:
            console.print("[red]✗ Failed to reset database.[/red]")
            raise typer.Exit(1)
            
    except Exception as e:
        console.print(f"[red]✗ Database reset failed: {e}[/red]")
        raise typer.Exit(1)


@app.command("purge-expired")
def purge_expired(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Purge expired raw downloads based on retention policy."""
    settings = _load_settings(config)
    
    try:
        from deliverect_sync.security.pii import DataRetentionManager
        manager = DataRetentionManager(settings.downloads_dir, settings.privacy.raw_download_retention_days)
        
        deleted = manager.purge_expired_downloads()
        if deleted:
            console.print(f"[green]✓ Purged {len(deleted)} expired files.[/green]")
        else:
            console.print("[dim]No expired files to purge.[/dim]")
            
    except Exception as e:
        console.print(f"[red]✗ Purge failed: {e}[/red]")
        raise typer.Exit(1)


def _colorize_status(status: RunStatus) -> str:
    """Apply rich color to a status code."""
    if status == RunStatus.SUCCESS:
        return f"[green]{status.value}[/green]"
    elif status == RunStatus.SUCCESS_WITH_WARNINGS:
        return f"[yellow]{status.value}[/yellow]"
    elif status == RunStatus.IN_PROGRESS:
        return f"[blue]{status.value}[/blue]"
    elif status in (RunStatus.NO_ORDERS, RunStatus.CANCELED_BY_USER):
        return f"[dim]{status.value}[/dim]"
    else:
        return f"[red]{status.value}[/red]"


def _display_run_result(result: "SyncRun", run_id: str) -> None:
    """Display a formatted run result summary."""
    from deliverect_sync.models import SyncRun

    if not isinstance(result, SyncRun):
        return

    status_text = _colorize_status(result.result)

    table = Table(title=f"Run {run_id[:8]} Complete", show_header=False, border_style="green")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Status", status_text)
    table.add_row("Imported Rows", str(result.imported_rows))
    table.add_row("New Orders", str(result.new_orders))
    table.add_row("Updated Orders", str(result.updated_orders))
    table.add_row("Rejected Rows", str(result.rejected_rows))

    if result.downloaded_filename:
        table.add_row("Downloaded File", result.downloaded_filename)
    if result.file_hash:
        table.add_row("File Hash", result.file_hash[:16] + "...")
    if result.error_message:
        table.add_row("Error", f"[red]{result.error_message}[/red]")

    console.print(table)


if __name__ == "__main__":
    app()
