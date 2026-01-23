"""
Schema extraction and conversion CLI.

Command-line interface for extracting PeopleSoft schema from DB2 and converting to PostgreSQL DDL.

Usage:
    python -m src.schema extract --output schema/extracted/
    python -m src.schema convert --input schema/extracted/ --output schema/postgres/
    python -m src.schema full --output schema/postgres/
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from config.settings import get_settings
from src.schema.converter import SchemaConverter
from src.schema.extractor import SchemaExtractor, ViewDefinition
from src.schema.generator import DDLGenerator
from src.utils.logging_config import setup_logging

console = Console()


def extract_schema(args: argparse.Namespace) -> int:
    """
    Extract schema from PeopleSoft catalog tables.

    Args:
        args: Command-line arguments

    Returns:
        int: Exit code
    """
    settings = get_settings()

    # Initialize logging
    setup_logging(
        log_level=settings.migration.log_level,
        log_format=settings.migration.log_format,
        log_file=settings.migration.log_dir / "schema_extraction.log",
    )

    console.print("\n[bold cyan]PeopleSoft Schema Extraction[/bold cyan]\n")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse table list if provided
    table_names = None
    if hasattr(args, "tables") and args.tables:
        table_names = [t.strip() for t in args.tables.split(",")]
        console.print(f"[yellow]Extracting specific tables: {', '.join(table_names)}[/yellow]\n")

    # Parse view list if provided
    view_names = None
    if hasattr(args, "views") and args.views:
        view_names = [v.strip() for v in args.views.split(",")]
        console.print(f"[yellow]Extracting specific views: {', '.join(view_names)}[/yellow]\n")

    # Determine what to extract
    include_views = getattr(args, "include_views", False)
    views_only = getattr(args, "views_only", False)
    extract_tables = not views_only

    try:
        extractor = SchemaExtractor(settings.db2)

        table_definitions = {}
        view_definitions = {}

        # Extract tables (unless views_only is set)
        if extract_tables:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Extracting tables from DB2...", total=None)
                table_definitions = extractor.extract_all_tables(table_names=table_names)
                progress.update(task, description=f"Extracted {len(table_definitions)} tables")

        # Extract views if requested
        if include_views or views_only:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Extracting views from DB2...", total=None)
                view_definitions = extractor.extract_all_views(view_names=view_names)
                progress.update(task, description=f"Extracted {len(view_definitions)} views")

        # Save extracted schema to JSON
        output_file = output_dir / "schema.json"

        # Convert tables to serializable format
        schema_data: dict = {"tables": {}, "views": {}}

        for record_name, table_def in table_definitions.items():
            schema_data["tables"][record_name] = {
                "record_name": table_def.record_name,
                "sql_table_name": table_def.sql_table_name,
                "description": table_def.description,
                "record_type": table_def.record_type,
                "key_fields": table_def.key_fields,
                "fields": [
                    {
                        "field_name": f.field_name,
                        "field_type": f.field_type,
                        "length": f.length,
                        "decimal_pos": f.decimal_pos,
                        "field_num": f.field_num,
                        "is_key": f.is_key,
                        "use_edit": f.use_edit,
                        "subrecord": f.subrecord,
                    }
                    for f in table_def.fields
                ],
            }

        # Convert views to serializable format
        for record_name, view_def in view_definitions.items():
            schema_data["views"][record_name] = {
                "record_name": view_def.record_name,
                "sql_view_name": view_def.sql_view_name,
                "description": view_def.description,
                "db2_sql_text": view_def.db2_sql_text,
                "effdt": view_def.effdt,
                "record_type": view_def.record_type,
                "fields": [
                    {
                        "field_name": f.field_name,
                        "field_type": f.field_type,
                        "length": f.length,
                        "decimal_pos": f.decimal_pos,
                        "field_num": f.field_num,
                        "is_key": f.is_key,
                        "use_edit": f.use_edit,
                        "subrecord": f.subrecord,
                    }
                    for f in view_def.fields
                ],
            }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(schema_data, f, indent=2)

        console.print(f"\n[green]✓[/green] Schema extracted successfully")
        if table_definitions:
            console.print(f"[green]✓[/green] Tables: {len(table_definitions)}")
        if view_definitions:
            console.print(f"[green]✓[/green] Views: {len(view_definitions)}")
        console.print(f"[green]✓[/green] Output: {output_file}\n")

        return 0

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n", style="red")
        return 1


def convert_schema(args: argparse.Namespace) -> int:
    """
    Convert extracted schema to PostgreSQL DDL.

    Args:
        args: Command-line arguments

    Returns:
        int: Exit code
    """
    settings = get_settings()

    # Initialize logging
    setup_logging(
        log_level=settings.migration.log_level,
        log_format=settings.migration.log_format,
        log_file=settings.migration.log_dir / "schema_conversion.log",
    )

    console.print("\n[bold cyan]Schema Conversion to PostgreSQL[/bold cyan]\n")

    input_file = Path(args.input) / "schema.json"
    output_dir = Path(args.output)

    if not input_file.exists():
        console.print(f"[bold red]Error:[/bold red] Schema file not found: {input_file}\n", style="red")
        return 1

    try:
        # Load extracted schema
        with open(input_file, "r", encoding="utf-8") as f:
            schema_data = json.load(f)

        # Handle both old format (flat) and new format (with tables/views)
        if "tables" in schema_data:
            tables_data = schema_data.get("tables", {})
            views_data = schema_data.get("views", {})
        else:
            # Old format - treat all as tables
            tables_data = schema_data
            views_data = {}

        console.print(f"[yellow]Loaded schema with {len(tables_data)} tables and {len(views_data)} views[/yellow]\n")

        # Reconstruct TableDefinition objects
        from src.schema.extractor import FieldDefinition, TableDefinition

        table_definitions = {}
        for record_name, data in tables_data.items():
            fields = [
                FieldDefinition(
                    field_name=f["field_name"],
                    field_type=f["field_type"],
                    length=f.get("length"),
                    decimal_pos=f.get("decimal_pos"),
                    field_num=f["field_num"],
                    is_key=f.get("is_key", False),
                    use_edit=f.get("use_edit"),
                    subrecord=f.get("subrecord"),
                )
                for f in data["fields"]
            ]

            table_def = TableDefinition(
                record_name=data["record_name"],
                sql_table_name=data["sql_table_name"],
                description=data.get("description"),
                fields=fields,
                key_fields=data.get("key_fields", []),
                record_type=data.get("record_type", 0),
            )
            table_definitions[record_name] = table_def

        # Reconstruct ViewDefinition objects
        view_definitions = {}
        for record_name, data in views_data.items():
            fields = [
                FieldDefinition(
                    field_name=f["field_name"],
                    field_type=f["field_type"],
                    length=f.get("length"),
                    decimal_pos=f.get("decimal_pos"),
                    field_num=f["field_num"],
                    is_key=f.get("is_key", False),
                    use_edit=f.get("use_edit"),
                    subrecord=f.get("subrecord"),
                )
                for f in data["fields"]
            ]

            view_def = ViewDefinition(
                record_name=data["record_name"],
                sql_view_name=data["sql_view_name"],
                description=data.get("description"),
                db2_sql_text=data.get("db2_sql_text", ""),
                fields=fields,
                effdt=data.get("effdt"),
                record_type=data.get("record_type", 1),
            )
            view_definitions[record_name] = view_def

        # Convert to PostgreSQL
        converter = SchemaConverter(
            convert_effdt_nulls=args.convert_effdt_nulls,
            use_lowercase=not args.keep_uppercase,
        )

        pg_tables = {}
        pg_views = {}

        # Convert tables
        if table_definitions:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Converting tables to PostgreSQL...", total=None)

                pg_tables = converter.convert_all_tables(table_definitions)

                # Add primary key indexes
                for pg_table in pg_tables.values():
                    converter.add_indexes_for_keys(pg_table)

                progress.update(task, description=f"Converted {len(pg_tables)} tables")

        # Convert views
        if view_definitions:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Converting views to PostgreSQL...", total=None)

                pg_views = converter.convert_all_views(view_definitions)

                progress.update(task, description=f"Converted {len(pg_views)} views")

        # Generate DDL
        generator = DDLGenerator()

        # Generate table DDL
        if pg_tables:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Generating table DDL...", total=None)
                generator.generate_all_tables_ddl(pg_tables, output_dir)
                progress.update(task, description=f"Generated DDL for {len(pg_tables)} tables")

        # Generate view DDL
        if pg_views:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Generating view DDL...", total=None)
                generator.generate_all_views_ddl(pg_views, output_dir)
                progress.update(task, description=f"Generated DDL for {len(pg_views)} views")

        console.print(f"\n[green]✓[/green] Schema converted successfully")
        if pg_tables:
            console.print(f"[green]✓[/green] Tables: {len(pg_tables)}")
            console.print(f"[green]✓[/green] Table DDL: {output_dir / 'all_tables.sql'}")
        if pg_views:
            console.print(f"[green]✓[/green] Views: {len(pg_views)}")
            console.print(f"[green]✓[/green] View DDL: {output_dir / 'all_views.sql'}")
        console.print(f"[green]✓[/green] Output directory: {output_dir}\n")

        return 0

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n", style="red")
        import traceback
        traceback.print_exc()
        return 1


def full_pipeline(args: argparse.Namespace) -> int:
    """
    Run full extraction and conversion pipeline.

    Args:
        args: Command-line arguments

    Returns:
        int: Exit code
    """
    # Create temporary extraction directory
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract
        extract_args = argparse.Namespace(
            output=temp_dir,
            tables=getattr(args, "tables", None),
            views=getattr(args, "views", None),
            include_views=getattr(args, "include_views", False),
            views_only=getattr(args, "views_only", False),
        )
        result = extract_schema(extract_args)
        if result != 0:
            return result

        # Convert
        convert_args = argparse.Namespace(
            input=temp_dir,
            output=args.output,
            convert_effdt_nulls=args.convert_effdt_nulls,
            keep_uppercase=args.keep_uppercase,
        )
        return convert_schema(convert_args)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PeopleSoft Schema Extraction and Conversion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract schema from DB2")
    extract_parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output directory for extracted schema",
    )
    extract_parser.add_argument(
        "--tables",
        "-t",
        help="Comma-separated list of specific tables to extract (optional)",
    )
    extract_parser.add_argument(
        "--views",
        "-v",
        help="Comma-separated list of specific views to extract (optional)",
    )
    extract_parser.add_argument(
        "--include-views",
        action="store_true",
        help="Include SQL views in extraction (RECTYPE=1)",
    )
    extract_parser.add_argument(
        "--views-only",
        action="store_true",
        help="Extract only SQL views, not tables",
    )

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert schema to PostgreSQL DDL")
    convert_parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Input directory with extracted schema",
    )
    convert_parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output directory for PostgreSQL DDL",
    )
    convert_parser.add_argument(
        "--convert-effdt-nulls",
        action="store_true",
        help="Convert EFFDT 1900-01-01 to NULL (default: preserve as-is)",
    )
    convert_parser.add_argument(
        "--keep-uppercase",
        action="store_true",
        help="Keep uppercase table/column names (default: convert to lowercase)",
    )

    # Full pipeline command
    full_parser = subparsers.add_parser("full", help="Extract and convert in one step")
    full_parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output directory for PostgreSQL DDL",
    )
    full_parser.add_argument(
        "--tables",
        "-t",
        help="Comma-separated list of specific tables (optional)",
    )
    full_parser.add_argument(
        "--views",
        "-v",
        help="Comma-separated list of specific views to extract (optional)",
    )
    full_parser.add_argument(
        "--include-views",
        action="store_true",
        help="Include SQL views in extraction",
    )
    full_parser.add_argument(
        "--views-only",
        action="store_true",
        help="Extract only SQL views, not tables",
    )
    full_parser.add_argument(
        "--convert-effdt-nulls",
        action="store_true",
        help="Convert EFFDT 1900-01-01 to NULL",
    )
    full_parser.add_argument(
        "--keep-uppercase",
        action="store_true",
        help="Keep uppercase names",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Execute command
    if args.command == "extract":
        return extract_schema(args)
    elif args.command == "convert":
        return convert_schema(args)
    elif args.command == "full":
        return full_pipeline(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
