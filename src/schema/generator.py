"""
PostgreSQL DDL generator.

Generates CREATE TABLE and CREATE INDEX statements from PostgreSQL table definitions.
Outputs well-formatted DDL with comments and proper constraints.
"""

from pathlib import Path

import structlog

from src.schema.converter import PostgresFieldDefinition, PostgresTableDefinition

log = structlog.get_logger()


class DDLGenerator:
    """
    Generate PostgreSQL DDL statements.

    Creates CREATE TABLE, CREATE INDEX, and other DDL statements.
    """

    def __init__(self, indent: str = "    "):
        """
        Initialize DDL generator.

        Args:
            indent: Indentation string for formatted DDL
        """
        self.indent = indent
        log.info("ddl_generator_initialized")

    def generate_create_table(
        self, pg_table: PostgresTableDefinition, include_indexes: bool = False
    ) -> str:
        """
        Generate CREATE TABLE statement.

        Args:
            pg_table: PostgreSQL table definition
            include_indexes: If True, include CREATE INDEX statements

        Returns:
            str: Complete CREATE TABLE DDL

        Example:
            CREATE TABLE ps_voucher (
                business_unit VARCHAR(5) NOT NULL,
                voucher_id VARCHAR(10) NOT NULL,
                invoice_id VARCHAR(30),
                invoice_dt DATE,
                PRIMARY KEY (business_unit, voucher_id)
            );
        """
        log.debug("generating_create_table", table_name=pg_table.table_name)

        ddl_lines = []

        # Table comment if available
        if pg_table.comment:
            ddl_lines.append(f"-- {pg_table.comment}")

        # CREATE TABLE
        ddl_lines.append(f"CREATE TABLE {pg_table.table_name} (")

        # Fields
        field_ddls = []
        for field in pg_table.fields:
            field_ddl = self.indent + field.to_ddl()
            field_ddls.append(field_ddl)

        # Primary key constraint
        if pg_table.primary_key_fields:
            pk_cols = ", ".join(pg_table.primary_key_fields)
            pk_ddl = f"{self.indent}PRIMARY KEY ({pk_cols})"
            field_ddls.append(pk_ddl)

        # Join field DDLs with commas
        ddl_lines.append(",\n".join(field_ddls))
        ddl_lines.append(");")

        # Table comment (PostgreSQL COMMENT syntax)
        if pg_table.comment:
            comment_sql = f"COMMENT ON TABLE {pg_table.table_name} IS '{self._escape_sql_string(pg_table.comment)}';"
            ddl_lines.append("")
            ddl_lines.append(comment_sql)

        # Field comments
        for field in pg_table.fields:
            if field.comment:
                comment_sql = (
                    f"COMMENT ON COLUMN {pg_table.table_name}.{field.field_name} IS "
                    f"'{self._escape_sql_string(field.comment)}';"
                )
                ddl_lines.append(comment_sql)

        ddl = "\n".join(ddl_lines)

        log.debug("create_table_generated", table_name=pg_table.table_name, lines=len(ddl_lines))

        return ddl

    def generate_create_index(
        self, table_name: str, index_name: str, columns: list[str], unique: bool = False
    ) -> str:
        """
        Generate CREATE INDEX statement.

        Args:
            table_name: Table name
            index_name: Index name
            columns: List of column names
            unique: If True, create UNIQUE index

        Returns:
            str: CREATE INDEX DDL

        Example:
            CREATE INDEX ps_voucher_effdt_idx ON ps_voucher (effdt);
        """
        unique_clause = "UNIQUE " if unique else ""
        cols = ", ".join(columns)
        ddl = f"CREATE {unique_clause}INDEX {index_name} ON {table_name} ({cols});"

        log.debug("create_index_generated", index_name=index_name, table_name=table_name)

        return ddl

    def generate_indexes_for_table(self, pg_table: PostgresTableDefinition) -> str:
        """
        Generate all CREATE INDEX statements for a table.

        Args:
            pg_table: PostgreSQL table definition

        Returns:
            str: DDL for all indexes
        """
        if not pg_table.indexes:
            return ""

        ddl_lines = []
        ddl_lines.append(f"-- Indexes for {pg_table.table_name}")

        for index_name, columns in pg_table.indexes:
            index_ddl = self.generate_create_index(
                table_name=pg_table.table_name,
                index_name=index_name,
                columns=columns,
            )
            ddl_lines.append(index_ddl)

        return "\n".join(ddl_lines)

    def generate_drop_indexes_for_table(self, pg_table: PostgresTableDefinition) -> str:
        """
        Generate DROP INDEX statements for bulk load optimization.

        Args:
            pg_table: PostgreSQL table definition

        Returns:
            str: DROP INDEX statements
        """
        if not pg_table.indexes:
            return ""

        ddl_lines = []
        ddl_lines.append(f"-- Drop indexes for {pg_table.table_name} (bulk load optimization)")

        for index_name, _ in pg_table.indexes:
            ddl_lines.append(f"DROP INDEX IF EXISTS {index_name};")

        return "\n".join(ddl_lines)

    def _escape_sql_string(self, s: str) -> str:
        """
        Escape string for SQL (replace single quotes with double single quotes).

        Args:
            s: String to escape

        Returns:
            str: Escaped string
        """
        return s.replace("'", "''")

    def save_table_ddl(
        self, pg_table: PostgresTableDefinition, output_dir: Path, include_indexes: bool = True
    ) -> None:
        """
        Save table DDL to file.

        Args:
            pg_table: PostgreSQL table definition
            output_dir: Output directory
            include_indexes: If True, generate separate index file
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save table DDL
        table_file = output_dir / "tables" / f"{pg_table.table_name}.sql"
        table_file.parent.mkdir(parents=True, exist_ok=True)

        table_ddl = self.generate_create_table(pg_table, include_indexes=False)

        with open(table_file, "w", encoding="utf-8") as f:
            f.write(table_ddl)
            f.write("\n")

        log.debug("table_ddl_saved", table_name=pg_table.table_name, file=str(table_file))

        # Save indexes separately if requested
        if include_indexes and pg_table.indexes:
            index_file = output_dir / "indexes" / f"{pg_table.table_name}.sql"
            index_file.parent.mkdir(parents=True, exist_ok=True)

            index_ddl = self.generate_indexes_for_table(pg_table)

            with open(index_file, "w", encoding="utf-8") as f:
                f.write(index_ddl)
                f.write("\n")

            log.debug("index_ddl_saved", table_name=pg_table.table_name, file=str(index_file))

    def generate_all_tables_ddl(
        self, pg_tables: dict[str, PostgresTableDefinition], output_dir: Path
    ) -> None:
        """
        Generate DDL for all tables and save to files.

        Creates:
        - output_dir/tables/*.sql - Individual table DDL
        - output_dir/indexes/*.sql - Individual index DDL
        - output_dir/all_tables.sql - Combined table DDL
        - output_dir/all_indexes.sql - Combined index DDL
        - output_dir/drop_indexes.sql - DROP INDEX statements for bulk load

        Args:
            pg_tables: Dictionary of PostgreSQL table definitions
            output_dir: Output directory
        """
        log.info("generating_all_tables_ddl", count=len(pg_tables), output_dir=str(output_dir))

        output_dir.mkdir(parents=True, exist_ok=True)

        all_tables_ddl = []
        all_indexes_ddl = []
        drop_indexes_ddl = []

        # Generate DDL for each table
        for record_name, pg_table in sorted(pg_tables.items()):
            try:
                # Save individual files
                self.save_table_ddl(pg_table, output_dir, include_indexes=True)

                # Accumulate for combined files
                table_ddl = self.generate_create_table(pg_table, include_indexes=False)
                all_tables_ddl.append(table_ddl)

                if pg_table.indexes:
                    index_ddl = self.generate_indexes_for_table(pg_table)
                    all_indexes_ddl.append(index_ddl)

                    drop_ddl = self.generate_drop_indexes_for_table(pg_table)
                    drop_indexes_ddl.append(drop_ddl)

            except Exception as e:
                log.error(
                    "ddl_generation_failed",
                    record_name=record_name,
                    table_name=pg_table.table_name,
                    error=str(e),
                )
                continue

        # Save combined files
        self._save_combined_file(
            output_dir / "all_tables.sql",
            all_tables_ddl,
            "All PeopleSoft tables",
        )

        if all_indexes_ddl:
            self._save_combined_file(
                output_dir / "all_indexes.sql",
                all_indexes_ddl,
                "All indexes for PeopleSoft tables",
            )

        if drop_indexes_ddl:
            self._save_combined_file(
                output_dir / "drop_indexes.sql",
                drop_indexes_ddl,
                "Drop all indexes (for bulk load optimization)",
            )

        log.info("all_tables_ddl_generated", count=len(pg_tables), output_dir=str(output_dir))

    def _save_combined_file(
        self, file_path: Path, ddl_blocks: list[str], header_comment: str
    ) -> None:
        """
        Save combined DDL file with header.

        Args:
            file_path: Output file path
            ddl_blocks: List of DDL blocks to combine
            header_comment: Header comment for file
        """
        with open(file_path, "w", encoding="utf-8") as f:
            # Write header
            f.write("--\n")
            f.write(f"-- {header_comment}\n")
            f.write("-- Generated by PS82-DB2-to-Postgres Migration Tool\n")
            f.write("--\n\n")

            # Write each block separated by blank lines
            for ddl in ddl_blocks:
                f.write(ddl)
                f.write("\n\n")

        log.info("combined_ddl_saved", file=str(file_path), blocks=len(ddl_blocks))
