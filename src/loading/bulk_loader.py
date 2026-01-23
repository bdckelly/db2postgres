"""
PostgreSQL bulk loading using COPY command.

Provides high-performance bulk loading with:
- PostgreSQL COPY FROM protocol
- Index management (drop before, rebuild after)
- Transaction management
- Error handling and rejected rows
- Progress tracking
"""

from pathlib import Path
from typing import Any

import structlog

from config.postgres_connection import PostgresConnectionError, get_bulk_load_connection
from config.settings import PostgresSettings, SSHTunnelSettings
from src.schema.converter import SchemaConverter
from src.schema.extractor import TableDefinition
from src.schema.generator import DDLGenerator

log = structlog.get_logger()


class BulkLoadError(Exception):
    """Bulk load operation failed."""

    pass


class BulkLoader:
    """
    High-performance bulk loader for PostgreSQL using COPY.

    Optimizations:
    - Uses COPY FROM STDIN for maximum throughput
    - Can drop/rebuild indexes for faster loading
    - Configurable transaction size
    - Tracks rejected rows
    """

    def __init__(
        self,
        postgres_settings: PostgresSettings,
        table_name: str,
        drop_indexes: bool = False,
        ssh_settings: SSHTunnelSettings | None = None,
    ):
        """
        Initialize bulk loader.

        Args:
            postgres_settings: PostgreSQL connection settings
            table_name: Target table name (lowercase)
            drop_indexes: If True, drop indexes before load and rebuild after
            ssh_settings: SSH tunnel settings for remote PostgreSQL (optional)
        """
        self.postgres_settings = postgres_settings
        self.ssh_settings = ssh_settings
        self.table_name = table_name
        self.drop_indexes = drop_indexes
        self.rows_loaded = 0
        self.rows_rejected = 0
        self.dropped_indexes = []

        log.info(
            "bulk_loader_initialized",
            table=table_name,
            drop_indexes=drop_indexes,
            ssh_tunnel=ssh_settings.tunnel_enabled if ssh_settings else False,
        )

    def table_exists(self) -> bool:
        """
        Check if table exists in PostgreSQL.

        Returns:
            bool: True if table exists, False otherwise
        """
        log.debug("checking_table_exists", table=self.table_name)

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                query = """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = %s
                    )
                """

                cursor.execute(query, (self.table_name,))
                result = cursor.fetchone()
                exists = result[0] if result else False

                log.debug("table_exists_check", table=self.table_name, exists=exists)
                return exists

        except Exception as e:
            log.error("table_exists_check_failed", table=self.table_name, error=str(e))
            # Assume table doesn't exist on error
            return False

    def create_table(self, table_def: TableDefinition) -> None:
        """
        Create table in PostgreSQL from TableDefinition.

        Uses SchemaConverter and DDLGenerator to convert DB2 definition
        to PostgreSQL DDL and execute it.

        Args:
            table_def: Source table definition from DB2

        Raises:
            BulkLoadError: If table creation fails
        """
        log.info("creating_table", table=self.table_name, source_table=table_def.sql_table_name)

        try:
            # Convert table definition to PostgreSQL
            converter = SchemaConverter(convert_effdt_nulls=False, use_lowercase=True)
            pg_table = converter.convert_table(table_def)

            # Generate DDL
            generator = DDLGenerator()
            ddl = generator.generate_create_table(pg_table, include_indexes=False)

            log.debug("generated_ddl", table=self.table_name, ddl_length=len(ddl))

            # Execute DDL
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(ddl)
                conn.commit()

            log.info(
                "table_created",
                table=self.table_name,
                fields=len(table_def.fields),
                keys=len(table_def.key_fields),
            )

        except Exception as e:
            log.error("table_creation_failed", table=self.table_name, error=str(e))
            raise BulkLoadError(f"Failed to create table {self.table_name}: {e}") from e

    def ensure_table_exists(self, table_def: TableDefinition | None = None) -> bool:
        """
        Ensure table exists, creating it if necessary.

        Args:
            table_def: Source table definition (required if table needs to be created)

        Returns:
            bool: True if table was created, False if it already existed

        Raises:
            BulkLoadError: If table doesn't exist and no table_def provided
        """
        if self.table_exists():
            log.debug("table_already_exists", table=self.table_name)
            return False

        if table_def is None:
            raise BulkLoadError(
                f"Table {self.table_name} does not exist and no table definition provided for auto-creation"
            )

        self.create_table(table_def)
        return True

    def get_table_indexes(self) -> list[tuple[str, str]]:
        """
        Get list of indexes for table.

        Returns:
            list: List of (index_name, index_definition) tuples
        """
        log.debug("fetching_indexes", table=self.table_name)

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                query = """
                    SELECT
                        indexname,
                        indexdef
                    FROM pg_indexes
                    WHERE tablename = %s
                    AND schemaname = 'public'
                """

                cursor.execute(query, (self.table_name,))
                indexes = cursor.fetchall()

                log.debug("indexes_fetched", table=self.table_name, count=len(indexes))
                return indexes

        except Exception as e:
            log.error("index_fetch_failed", table=self.table_name, error=str(e))
            return []

    def drop_table_indexes(self) -> None:
        """Drop all indexes on table (except primary key)."""
        if not self.drop_indexes:
            return

        log.info("dropping_indexes", table=self.table_name)

        indexes = self.get_table_indexes()

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                for index_name, index_def in indexes:
                    # Skip primary key indexes (they end with _pkey)
                    if index_name.endswith("_pkey"):
                        log.debug("skipping_primary_key_index", index_name=index_name)
                        continue

                    try:
                        cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
                        self.dropped_indexes.append((index_name, index_def))
                        log.debug("index_dropped", index_name=index_name)

                    except Exception as e:
                        log.warning("index_drop_failed", index_name=index_name, error=str(e))

                conn.commit()

                log.info(
                    "indexes_dropped",
                    table=self.table_name,
                    count=len(self.dropped_indexes),
                )

        except Exception as e:
            log.error("index_drop_failed", table=self.table_name, error=str(e))
            raise BulkLoadError(f"Failed to drop indexes: {e}") from e

    def rebuild_indexes(self) -> None:
        """Rebuild indexes that were dropped."""
        if not self.dropped_indexes:
            log.debug("no_indexes_to_rebuild", table=self.table_name)
            return

        log.info("rebuilding_indexes", table=self.table_name, count=len(self.dropped_indexes))

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                for index_name, index_def in self.dropped_indexes:
                    try:
                        cursor.execute(index_def)
                        log.debug("index_rebuilt", index_name=index_name)

                    except Exception as e:
                        log.error(
                            "index_rebuild_failed",
                            index_name=index_name,
                            error=str(e),
                        )
                        # Continue with other indexes

                conn.commit()

                log.info("indexes_rebuilt", table=self.table_name)

        except Exception as e:
            log.error("index_rebuild_failed", table=self.table_name, error=str(e))
            # Don't raise - indexes can be rebuilt manually if needed

    def load_from_csv(
        self,
        csv_file: Path,
        delimiter: str = ",",
        null_string: str = "\\N",
    ) -> int:
        """
        Load data from CSV file using COPY.

        Args:
            csv_file: Path to CSV file
            delimiter: Field delimiter
            null_string: String representing NULL

        Returns:
            int: Number of rows loaded

        Raises:
            BulkLoadError: If load fails
        """
        if not csv_file.exists():
            raise BulkLoadError(f"CSV file not found: {csv_file}")

        log.info("bulk_load_started", table=self.table_name, csv_file=str(csv_file))

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                # Open CSV file and use COPY FROM
                with open(csv_file, "r", encoding="utf-8") as f:
                    copy_sql = f"""
                        COPY {self.table_name}
                        FROM STDIN
                        WITH (
                            FORMAT CSV,
                            DELIMITER '{delimiter}',
                            NULL '{null_string}',
                            ENCODING 'UTF8'
                        )
                    """

                    # Use psycopg's copy protocol
                    with cursor.copy(copy_sql) as copy:
                        while True:
                            line = f.readline()
                            if not line:
                                break
                            copy.write(line)

                # Commit transaction
                conn.commit()

                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                self.rows_loaded = cursor.fetchone()[0]

                log.info(
                    "bulk_load_completed",
                    table=self.table_name,
                    rows_loaded=self.rows_loaded,
                    csv_file=str(csv_file),
                )

                return self.rows_loaded

        except Exception as e:
            log.error(
                "bulk_load_failed",
                table=self.table_name,
                csv_file=str(csv_file),
                error=str(e),
            )
            raise BulkLoadError(f"Bulk load failed: {e}") from e

    def load_from_multiple_csvs(
        self,
        csv_files: list[Path],
        delimiter: str = ",",
        null_string: str = "\\N",
    ) -> int:
        """
        Load data from multiple CSV files (chunked extraction).

        Args:
            csv_files: List of CSV file paths
            delimiter: Field delimiter
            null_string: String representing NULL

        Returns:
            int: Total number of rows loaded
        """
        log.info(
            "multi_file_bulk_load_started",
            table=self.table_name,
            file_count=len(csv_files),
        )

        total_rows = 0

        for i, csv_file in enumerate(csv_files, 1):
            try:
                rows = self.load_from_csv(csv_file, delimiter, null_string)
                total_rows += rows

                log.info(
                    "chunk_loaded",
                    table=self.table_name,
                    chunk=i,
                    total_chunks=len(csv_files),
                    chunk_rows=rows,
                    total_rows=total_rows,
                )

            except Exception as e:
                log.error(
                    "chunk_load_failed",
                    table=self.table_name,
                    chunk=i,
                    csv_file=str(csv_file),
                    error=str(e),
                )
                raise

        self.rows_loaded = total_rows

        log.info(
            "multi_file_bulk_load_completed",
            table=self.table_name,
            total_rows=total_rows,
        )

        return total_rows

    def load_with_index_management(
        self,
        csv_files: list[Path] | Path,
        delimiter: str = ",",
        null_string: str = "\\N",
        table_def: TableDefinition | None = None,
    ) -> int:
        """
        Complete load process with index management.

        Args:
            csv_files: Single CSV file or list of CSV files
            delimiter: Field delimiter
            null_string: String representing NULL
            table_def: Source table definition for auto-creating table if it doesn't exist

        Returns:
            int: Number of rows loaded
        """
        log.info("load_with_index_management_started", table=self.table_name)

        try:
            # Ensure table exists (auto-create if table_def provided)
            table_created = self.ensure_table_exists(table_def)
            if table_created:
                log.info("table_auto_created", table=self.table_name)

            # Drop indexes if configured
            if self.drop_indexes:
                self.drop_table_indexes()

            # Load data
            if isinstance(csv_files, Path):
                rows_loaded = self.load_from_csv(csv_files, delimiter, null_string)
            else:
                rows_loaded = self.load_from_multiple_csvs(csv_files, delimiter, null_string)

            # Rebuild indexes
            if self.drop_indexes and self.dropped_indexes:
                self.rebuild_indexes()

            # Analyze table for query planner
            self.analyze_table()

            log.info(
                "load_with_index_management_completed",
                table=self.table_name,
                rows_loaded=rows_loaded,
            )

            return rows_loaded

        except Exception as e:
            log.error(
                "load_with_index_management_failed",
                table=self.table_name,
                error=str(e),
            )
            # Try to rebuild indexes even on failure
            if self.dropped_indexes:
                try:
                    self.rebuild_indexes()
                except Exception:
                    pass
            raise

    def analyze_table(self) -> None:
        """Run ANALYZE on table to update statistics."""
        log.info("analyzing_table", table=self.table_name)

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(f"ANALYZE {self.table_name}")
                conn.commit()

                log.info("table_analyzed", table=self.table_name)

        except Exception as e:
            log.warning("table_analyze_failed", table=self.table_name, error=str(e))

    def truncate_table(self) -> None:
        """Truncate table before loading (for re-runs)."""
        log.warning("truncating_table", table=self.table_name)

        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(f"TRUNCATE TABLE {self.table_name}")
                conn.commit()

                log.info("table_truncated", table=self.table_name)

        except Exception as e:
            log.error("table_truncate_failed", table=self.table_name, error=str(e))
            raise BulkLoadError(f"Failed to truncate table: {e}") from e


class StreamingLoader:
    """
    High-performance streaming loader for PostgreSQL using psycopg3's COPY protocol.

    Streams data directly from a generator to PostgreSQL without intermediate files.
    This is the most memory-efficient and fastest method for large data transfers.

    Key benefits:
    - Zero intermediate file I/O
    - Near-zero memory footprint (one row at a time)
    - Concurrent extract/load (no "wait for extract -> wait for load" bottleneck)
    - Automatic type conversion via psycopg3's write_row()
    """

    def __init__(
        self,
        postgres_settings: PostgresSettings,
        table_name: str,
        ssh_settings: SSHTunnelSettings | None = None,
    ):
        """
        Initialize streaming loader.

        Args:
            postgres_settings: PostgreSQL connection settings
            table_name: Target table name (lowercase)
            ssh_settings: SSH tunnel settings for remote PostgreSQL (optional)
        """
        self.postgres_settings = postgres_settings
        self.ssh_settings = ssh_settings
        self.table_name = table_name
        self.rows_loaded = 0
        self.bad_rows = 0

        log.info(
            "streaming_loader_initialized",
            table=table_name,
            ssh_tunnel=ssh_settings.tunnel_enabled if ssh_settings else False,
        )

    def table_exists(self) -> bool:
        """Check if table exists in PostgreSQL."""
        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                query = """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = %s
                    )
                """
                cursor.execute(query, (self.table_name,))
                result = cursor.fetchone()
                return result[0] if result else False
        except Exception as e:
            log.error("table_exists_check_failed", table=self.table_name, error=str(e))
            return False

    def get_row_count(self) -> int:
        """Get current row count in target table."""
        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            log.warning("row_count_check_failed", table=self.table_name, error=str(e))
            return 0

    def create_table(self, table_def: TableDefinition) -> None:
        """Create table in PostgreSQL from TableDefinition."""
        log.info("creating_table", table=self.table_name, source_table=table_def.sql_table_name)

        try:
            converter = SchemaConverter(convert_effdt_nulls=False, use_lowercase=True)
            pg_table = converter.convert_table(table_def)

            generator = DDLGenerator()
            ddl = generator.generate_create_table(pg_table, include_indexes=False)

            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(ddl)
                conn.commit()

            log.info("table_created", table=self.table_name, fields=len(table_def.fields))

        except Exception as e:
            log.error("table_creation_failed", table=self.table_name, error=str(e))
            raise BulkLoadError(f"Failed to create table {self.table_name}: {e}") from e

    def ensure_table_exists(self, table_def: TableDefinition | None = None) -> bool:
        """Ensure table exists, creating it if necessary."""
        if self.table_exists():
            log.debug("table_already_exists", table=self.table_name)
            return False

        if table_def is None:
            raise BulkLoadError(
                f"Table {self.table_name} does not exist and no table definition provided"
            )

        self.create_table(table_def)
        return True

    def truncate_table(self) -> None:
        """Truncate table before loading."""
        log.warning("truncating_table", table=self.table_name)
        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(f"TRUNCATE TABLE {self.table_name}")
                conn.commit()
                log.info("table_truncated", table=self.table_name)
        except Exception as e:
            log.error("table_truncate_failed", table=self.table_name, error=str(e))
            raise BulkLoadError(f"Failed to truncate table: {e}") from e

    def stream_load(
        self,
        row_generator,
        table_def: TableDefinition | None = None,
        columns: list[str] | None = None,
        if_exists: str = "truncate",
    ) -> int:
        """
        Stream data from generator directly into PostgreSQL using COPY protocol.

        This is the "gold standard" for ETL - rows flow from source to target
        with minimal memory usage and maximum throughput.

        Args:
            row_generator: Generator yielding tuples of row data
            table_def: Table definition for auto-creating table if needed
            columns: Optional list of column names (for COPY column list)
            if_exists: How to handle existing data:
                - 'truncate': Clear table before loading (default, idempotent)
                - 'error': Fail if table has existing data
                - 'append': Add to existing data

        Returns:
            int: Number of rows loaded

        Raises:
            BulkLoadError: If if_exists='error' and table has data

        Example:
            >>> def extract_from_db2():
            ...     for row in db2_cursor.fetchall():
            ...         yield transform_row(row)
            >>> loader = StreamingLoader(pg_settings, "ps_voucher")
            >>> rows = loader.stream_load(extract_from_db2(), if_exists="truncate")
        """
        log.info("stream_load_started", table=self.table_name, if_exists=if_exists)

        self.rows_loaded = 0
        self.bad_rows = 0

        try:
            # Ensure table exists (auto-create if needed)
            table_created = self.ensure_table_exists(table_def)
            if table_created:
                log.info("table_auto_created", table=self.table_name)

            # Handle existing data based on if_exists setting
            if self.table_exists() and not table_created:
                existing_rows = self.get_row_count()
                if existing_rows > 0:
                    if if_exists == "error":
                        raise BulkLoadError(
                            f"Table {self.table_name} has {existing_rows} existing rows. "
                            f"Use --if-exists=truncate to clear data or --if-exists=append to add to it."
                        )
                    elif if_exists == "truncate":
                        log.info("truncating_existing_data", table=self.table_name, existing_rows=existing_rows)
                        self.truncate_table()
                    elif if_exists == "append":
                        log.warning("appending_to_existing_data", table=self.table_name, existing_rows=existing_rows)

            # Build COPY command
            if columns:
                col_list = ", ".join(columns)
                copy_sql = f"COPY {self.table_name} ({col_list}) FROM STDIN"
            else:
                copy_sql = f"COPY {self.table_name} FROM STDIN"

            log.debug("copy_sql", sql=copy_sql)

            # Stream data using psycopg3's COPY protocol
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()

                with cursor.copy(copy_sql) as copy:
                    for row in row_generator:
                        try:
                            copy.write_row(row)
                            self.rows_loaded += 1

                            # Progress logging every 100k rows
                            if self.rows_loaded % 100_000 == 0:
                                log.info(
                                    "streaming_progress",
                                    table=self.table_name,
                                    rows_loaded=self.rows_loaded,
                                )

                        except Exception as row_error:
                            # Log bad row but continue (optional: could re-raise)
                            self.bad_rows += 1
                            log.warning(
                                "bad_row_skipped",
                                table=self.table_name,
                                row_number=self.rows_loaded + self.bad_rows,
                                error=str(row_error),
                            )
                            # Uncomment to fail on first bad row:
                            # raise

                conn.commit()

            # Run ANALYZE for query planner
            self._analyze_table()

            log.info(
                "stream_load_completed",
                table=self.table_name,
                rows_loaded=self.rows_loaded,
                bad_rows=self.bad_rows,
            )

            return self.rows_loaded

        except Exception as e:
            log.error(
                "stream_load_failed",
                table=self.table_name,
                rows_loaded=self.rows_loaded,
                error=str(e),
            )
            raise BulkLoadError(f"Stream load failed: {e}") from e

    def _analyze_table(self) -> None:
        """Run ANALYZE on table to update statistics."""
        try:
            with get_bulk_load_connection(self.postgres_settings, self.ssh_settings, table_name=self.table_name) as conn:
                cursor = conn.cursor()
                cursor.execute(f"ANALYZE {self.table_name}")
                conn.commit()
                log.debug("table_analyzed", table=self.table_name)
        except Exception as e:
            log.warning("table_analyze_failed", table=self.table_name, error=str(e))


def load_table(
    postgres_settings: PostgresSettings,
    table_name: str,
    staging_dir: Path,
    drop_indexes: bool = True,
    truncate_first: bool = False,
) -> int:
    """
    Convenience function to load a table from staging directory.

    Args:
        postgres_settings: PostgreSQL settings
        table_name: Table name
        staging_dir: Staging directory containing CSV files
        drop_indexes: Whether to drop indexes during load
        truncate_first: Whether to truncate table before loading

    Returns:
        int: Number of rows loaded

    Example:
        >>> from config.settings import get_settings
        >>> settings = get_settings()
        >>> rows = load_table(settings.postgres, "ps_voucher", Path("data/staging"))
        >>> print(f"Loaded {rows} rows")
    """
    log.info("loading_table", table=table_name, staging_dir=str(staging_dir))

    loader = BulkLoader(postgres_settings, table_name, drop_indexes=drop_indexes)

    if truncate_first:
        loader.truncate_table()

    # Find all CSV files for this table
    table_staging_dir = staging_dir / table_name
    csv_files = sorted(table_staging_dir.glob("*.csv"))

    if not csv_files:
        log.warning("no_csv_files_found", table=table_name, staging_dir=str(table_staging_dir))
        return 0

    log.info("found_csv_files", table=table_name, count=len(csv_files))

    rows_loaded = loader.load_with_index_management(csv_files)

    return rows_loaded
