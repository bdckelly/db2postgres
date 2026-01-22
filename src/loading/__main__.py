"""
Validation CLI for migrated data.

Command-line interface for validating data integrity after migration.

Usage:
    python -m src.loading validate --mode counts
    python -m src.loading validate --mode checksums --sample-rate 0.01
    python -m src.loading validate --tables PS_VOUCHER,PS_VCHR_LINE
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config.settings import get_settings
from src.loading.validator import DataValidator, ValidationMode, print_validation_report
from src.schema.extractor import SchemaExtractor
from src.utils.logging_config import setup_logging

console = Console()


def validate_migration(args: argparse.Namespace) -> int:
    """
    Validate migrated data.

    Args:
        args: Command-line arguments

    Returns:
        int: Exit code (0 = all passed, 1 = some failed)
    """
    settings = get_settings()

    # Initialize logging
    setup_logging(
        log_level=settings.migration.log_level,
        log_format=settings.migration.log_format,
        log_file=settings.migration.log_dir / "validation.log",
    )

    console.print("\n[bold cyan]Data Validation[/bold cyan]\n")

    # Parse validation mode
    mode = ValidationMode(args.mode)
    sample_rate = args.sample_rate if args.sample_rate else 0.01

    console.print(f"[yellow]Validation Mode: {mode.value}[/yellow]")
    if mode == ValidationMode.CHECKSUM:
        console.print(f"[yellow]Sample Rate: {sample_rate * 100:.1f}%[/yellow]")
    console.print()

    try:
        # Get table list
        if args.tables:
            # Specific tables provided
            table_names = [t.strip() for t in args.tables.split(",")]
            console.print(f"[yellow]Validating {len(table_names)} specific tables[/yellow]\n")
        else:
            # Validate all tables
            console.print("[yellow]Extracting table list from DB2...[/yellow]\n")
            extractor = SchemaExtractor(settings.db2)
            table_names = extractor.extract_table_list()
            console.print(f"[yellow]Found {len(table_names)} tables to validate[/yellow]\n")

        # Build table pairs (DB2 name, PostgreSQL name)
        table_pairs = []
        for record_name in table_names:
            # Get SQL table name from PSRECDEFN
            metadata = SchemaExtractor(settings.db2).extract_table_metadata(record_name)
            if not metadata:
                console.print(f"[red]✗[/red] Could not find metadata for {record_name}")
                continue

            db2_table = metadata["sql_table_name"]
            postgres_table = db2_table.lower()
            table_pairs.append((db2_table, postgres_table))

        # Run validation
        validator = DataValidator(settings.db2, settings.postgres)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Validating {len(table_pairs)} tables...",
                total=len(table_pairs),
            )

            results = []
            for i, (db2_table, postgres_table) in enumerate(table_pairs, 1):
                progress.update(
                    task,
                    description=f"Validating {postgres_table} ({i}/{len(table_pairs)})...",
                    completed=i - 1,
                )

                result = validator.validate_table(
                    db2_table, postgres_table, mode=mode, sample_rate=sample_rate
                )
                results.append(result)

            progress.update(task, completed=len(table_pairs))

        # Display results
        display_validation_results(results)

        # Print detailed report
        if not args.quiet:
            print_validation_report(results)

        # Return exit code
        failed_count = sum(1 for r in results if not r.is_valid)
        if failed_count > 0:
            console.print(f"\n[bold red]Validation failed: {failed_count} tables[/bold red]\n")
            return 1
        else:
            console.print(f"\n[bold green]✓ All {len(results)} tables validated successfully![/bold green]\n")
            return 0

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n", style="red")
        import traceback
        traceback.print_exc()
        return 1


def display_validation_results(results: list) -> None:
    """
    Display validation results in a rich table.

    Args:
        results: List of ValidationResult objects
    """
    table = Table(title="Validation Results", show_header=True, header_style="bold magenta")
    table.add_column("Status", style="dim", width=6)
    table.add_column("Table", style="cyan")
    table.add_column("DB2 Rows", justify="right", style="green")
    table.add_column("PostgreSQL Rows", justify="right", style="green")
    table.add_column("Match", justify="center")

    for result in results:
        status = "✓" if result.is_valid else "✗"
        status_style = "green" if result.is_valid else "red"

        match = "✓" if result.is_valid else "✗"
        match_style = "green" if result.is_valid else "red"

        db2_rows = str(result.db2_row_count) if result.db2_row_count is not None else "N/A"
        pg_rows = str(result.postgres_row_count) if result.postgres_row_count is not None else "N/A"

        table.add_row(
            f"[{status_style}]{status}[/{status_style}]",
            result.table_name,
            db2_rows,
            pg_rows,
            f"[{match_style}]{match}[/{match_style}]",
        )

    console.print("\n")
    console.print(table)
    console.print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Data Validation for PS82 Migration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate migrated data")
    validate_parser.add_argument(
        "--mode",
        "-m",
        choices=["counts", "checksums", "detailed"],
        default="counts",
        help="Validation mode (default: counts)",
    )
    validate_parser.add_argument(
        "--tables",
        "-t",
        help="Comma-separated list of specific tables to validate (optional)",
    )
    validate_parser.add_argument(
        "--sample-rate",
        "-s",
        type=float,
        help="Sample rate for checksum validation (0.0-1.0, default: 0.01)",
    )
    validate_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress detailed report output",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Execute command
    if args.command == "validate":
        return validate_migration(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
