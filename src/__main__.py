"""
Main CLI entry point for PS82-DB2-to-Postgres migration tool.

Usage:
    python -m src                         # Run full migration
    python -m src --resume                # Resume from checkpoint
    python -m src --tables PS_VOUCHER,... # Migrate specific tables
"""

import argparse
import sys

from rich.console import Console

from config.settings import get_settings
from src.orchestrator import MigrationOrchestrator
from src.utils.logging_config import setup_logging

console = Console()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PS82-DB2-to-Postgres Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full migration with dynamic parallelism
  python -m src

  # Resume interrupted migration
  python -m src --resume

  # Migrate specific tables only
  python -m src --tables PS_VOUCHER,PS_VCHR_LINE,PS_PAYMENT_TBL

  # Override parallelism bounds
  python -m src --min-workers 5 --max-workers 15

  # Extract schema only (use src.schema module)
  python -m src.schema full --output schema/postgres/

  # Validate migrated data (use src.loading module)
  python -m src.loading validate --mode counts
        """,
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint (skip completed tables)",
    )

    parser.add_argument(
        "--tables",
        "-t",
        help="Comma-separated list of specific tables to migrate",
    )

    parser.add_argument(
        "--min-workers",
        type=int,
        help=f"Minimum worker processes (default: from config)",
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        help=f"Maximum worker processes (default: from config)",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        help="Chunk size for large tables (default: from config)",
    )

    args = parser.parse_args()

    # Load settings
    settings = get_settings()

    # Override settings from command line
    if args.min_workers:
        settings.migration.min_workers = args.min_workers

    if args.max_workers:
        settings.migration.max_workers = args.max_workers

    if args.chunk_size:
        settings.migration.chunk_size = args.chunk_size

    # Initialize logging
    setup_logging(
        log_level=settings.migration.log_level,
        log_format=settings.migration.log_format,
        log_file=settings.migration.log_dir / "migration.log",
    )

    # Print banner
    print_banner(settings, args)

    # Parse table list
    table_names = None
    if args.tables:
        table_names = [t.strip() for t in args.tables.split(",")]

    try:
        # Create and run orchestrator
        orchestrator = MigrationOrchestrator(
            settings=settings,
            table_names=table_names,
            resume=args.resume,
        )

        success = orchestrator.run()

        if success:
            console.print("\n[bold green]✓ Migration completed successfully![/bold green]\n")
            return 0
        else:
            console.print("\n[bold red]✗ Migration completed with errors.[/bold red]\n")
            console.print("[yellow]Check logs for details. Failed tables can be retried.[/yellow]\n")
            return 1

    except KeyboardInterrupt:
        console.print("\n[yellow]Migration interrupted by user.[/yellow]\n")
        return 130  # Standard exit code for Ctrl+C

    except Exception as e:
        console.print(f"\n[bold red]Fatal error: {e}[/bold red]\n")
        import traceback
        traceback.print_exc()
        return 1


def print_banner(settings, args) -> None:
    """Print application banner."""
    console.print("\n" + "=" * 80)
    console.print("[bold cyan]PS82-DB2-to-Postgres Migration Tool[/bold cyan]")
    console.print("=" * 80)

    console.print(f"\n[yellow]Configuration:[/yellow]")
    console.print(f"  DB2: {settings.db2.hostname}:{settings.db2.port}/{settings.db2.database}")
    console.print(f"  PostgreSQL: {settings.postgres.host}:{settings.postgres.port}/{settings.postgres.database}")
    console.print(f"  Workers: {settings.migration.min_workers}-{settings.migration.max_workers}")
    console.print(f"  Chunk Size: {settings.migration.chunk_size:,} rows")

    if args.resume:
        console.print(f"  [cyan]Mode: Resume from checkpoint[/cyan]")

    if args.tables:
        table_list = [t.strip() for t in args.tables.split(",")]
        console.print(f"  [cyan]Tables: {len(table_list)} specific tables[/cyan]")

    console.print()


if __name__ == "__main__":
    # Set multiprocessing start method
    import multiprocessing
    multiprocessing.set_start_method("spawn", force=True)

    sys.exit(main())
