"""
PostgreSQL connection management for bulk loading and validation.

Provides connection pooling and optimized settings for COPY operations.
"""

from contextlib import contextmanager
from typing import Any, Generator

import structlog

from config.settings import PostgresSettings

# Mock PostgreSQL connection for development
try:
    import psycopg
    from psycopg import Connection, Cursor

    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False
    psycopg = None
    Connection = None
    Cursor = None

log = structlog.get_logger()


class MockPostgresConnection:
    """Mock PostgreSQL connection for development without database access."""

    def __init__(self, conninfo: str):
        self.conninfo = conninfo
        self.closed = False
        log.warning("using_mock_postgres_connection", reason="psycopg not installed")

    def cursor(self):
        return MockPostgresCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class MockPostgresCursor:
    """Mock PostgreSQL cursor for development."""

    def execute(self, query: str, params: tuple = ()) -> None:
        log.debug("mock_postgres_query", query=query[:100], params=params)

    def fetchone(self) -> tuple | None:
        return None

    def fetchmany(self, size: int = 1) -> list[tuple]:
        return []

    def fetchall(self) -> list[tuple]:
        return []

    def copy(self, query: str):
        return MockCopyContext()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @property
    def description(self):
        return []


class MockCopyContext:
    """Mock COPY context for development."""

    def write(self, data: bytes | str):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class PostgresConnectionError(Exception):
    """PostgreSQL connection error."""

    pass


class PostgresQueryError(Exception):
    """PostgreSQL query execution error."""

    pass


def create_connection(settings: PostgresSettings) -> Any:
    """
    Create PostgreSQL connection.

    Args:
        settings: PostgreSQL connection settings

    Returns:
        PostgreSQL connection object (psycopg.Connection or mock)

    Raises:
        PostgresConnectionError: If connection fails
    """
    if not HAS_PSYCOPG:
        log.warning("psycopg_not_installed", message="Using mock PostgreSQL connection")
        conninfo = settings.connection_string()
        return MockPostgresConnection(conninfo)

    try:
        conninfo = settings.connection_string()
        log.info(
            "postgres_connection_attempt",
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )

        conn = psycopg.connect(conninfo)

        log.info(
            "postgres_connection_success",
            host=settings.host,
            database=settings.database,
        )
        return conn

    except Exception as e:
        error_msg = f"Failed to connect to PostgreSQL: {e}"
        log.error(
            "postgres_connection_failed",
            error=str(e),
            host=settings.host,
            database=settings.database,
        )
        raise PostgresConnectionError(error_msg) from e


@contextmanager
def get_postgres_connection(settings: PostgresSettings) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL connection with automatic cleanup.

    Usage:
        with get_postgres_connection(settings) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ps_voucher")

    Args:
        settings: PostgreSQL connection settings

    Yields:
        PostgreSQL connection object

    Raises:
        PostgresConnectionError: If connection fails
    """
    conn = None
    try:
        conn = create_connection(settings)
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
                log.debug("postgres_connection_closed")
            except Exception as e:
                log.warning("postgres_connection_close_error", error=str(e))


@contextmanager
def get_postgres_cursor(settings: PostgresSettings) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL cursor with automatic connection and cursor cleanup.

    Args:
        settings: PostgreSQL connection settings

    Yields:
        PostgreSQL cursor object

    Example:
        with get_postgres_cursor(settings) as cursor:
            cursor.execute("SELECT COUNT(*) FROM ps_voucher")
            count = cursor.fetchone()[0]
    """
    with get_postgres_connection(settings) as conn:
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            try:
                cursor.close()
                log.debug("postgres_cursor_closed")
            except Exception as e:
                log.warning("postgres_cursor_close_error", error=str(e))


@contextmanager
def get_bulk_load_connection(
    settings: PostgresSettings, table_name: str | None = None
) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL connection optimized for bulk loading.

    Applies optimizations for COPY operations:
    - Increases work_mem for better performance
    - Can disable triggers if needed
    - Optimized for write-heavy workloads

    Args:
        settings: PostgreSQL connection settings
        table_name: Optional table name for table-specific optimizations

    Yields:
        PostgreSQL connection object configured for bulk loading

    Example:
        with get_bulk_load_connection(settings, "ps_voucher") as conn:
            cursor = conn.cursor()
            with cursor.copy("COPY ps_voucher FROM STDIN WITH (FORMAT CSV)") as copy:
                for line in csv_file:
                    copy.write(line)
            conn.commit()
    """
    with get_postgres_connection(settings) as conn:
        cursor = conn.cursor()
        try:
            # Increase work_mem for better bulk load performance
            cursor.execute("SET work_mem = '256MB'")
            cursor.execute("SET maintenance_work_mem = '512MB'")

            # Disable synchronous commit for better performance (data already in staging)
            cursor.execute("SET synchronous_commit = OFF")

            log.info(
                "bulk_load_connection_configured",
                table=table_name,
                work_mem="256MB",
                maintenance_work_mem="512MB",
            )

            yield conn

        finally:
            try:
                cursor.close()
            except Exception as e:
                log.warning("bulk_load_cursor_close_error", error=str(e))


def test_connection(settings: PostgresSettings) -> bool:
    """
    Test PostgreSQL connection with a simple query.

    Args:
        settings: PostgreSQL connection settings

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        with get_postgres_cursor(settings) as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            log.info("postgres_connection_test_success", result=result)
            return True
    except Exception as e:
        log.error("postgres_connection_test_failed", error=str(e))
        return False


def get_table_row_count(settings: PostgresSettings, table_name: str) -> int:
    """
    Get row count for a PostgreSQL table.

    Args:
        settings: PostgreSQL connection settings
        table_name: Name of the table (lowercase, e.g. 'ps_voucher')

    Returns:
        int: Number of rows in the table

    Raises:
        PostgresQueryError: If query fails
    """
    try:
        with get_postgres_cursor(settings) as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            result = cursor.fetchone()
            count = result[0] if result else 0
            log.info("postgres_row_count", table=table_name, count=count)
            return count
    except Exception as e:
        error_msg = f"Failed to get row count for {table_name}: {e}"
        log.error("postgres_row_count_failed", table=table_name, error=str(e))
        raise PostgresQueryError(error_msg) from e
