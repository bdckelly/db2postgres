"""
DB2 z/OS connection management with retry logic and proper resource cleanup.

Provides connection pooling and error handling for mainframe DB2 connections.
"""

import time
from contextlib import contextmanager
from typing import Any, Generator

import structlog

from config.settings import DB2Settings

# Mock DB2 connection for development without DB2 access
try:
    import ibm_db
    import ibm_db_dbi

    HAS_IBM_DB = True
except ImportError:
    HAS_IBM_DB = False
    ibm_db = None
    ibm_db_dbi = None

log = structlog.get_logger()


class MockDB2Connection:
    """Mock DB2 connection for development without database access."""

    def __init__(self, conn_str: str):
        self.conn_str = conn_str
        self.closed = False
        log.warning("using_mock_db2_connection", reason="ibm_db not installed")

    def cursor(self):
        return MockDB2Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class MockDB2Cursor:
    """Mock DB2 cursor for development."""

    def execute(self, query: str, params: tuple = ()) -> None:
        log.debug("mock_db2_query", query=query[:100], params=params)

    def fetchone(self) -> tuple | None:
        return None

    def fetchmany(self, size: int = 1) -> list[tuple]:
        return []

    def fetchall(self) -> list[tuple]:
        return []

    def close(self):
        pass

    @property
    def description(self):
        return []


class DB2ConnectionError(Exception):
    """DB2 connection error."""

    pass


class DB2QueryError(Exception):
    """DB2 query execution error."""

    pass


def build_connection_string(settings: DB2Settings) -> str:
    """
    Build DB2 connection string from settings.

    Args:
        settings: DB2 connection settings

    Returns:
        str: DB2 connection string for ibm_db
    """
    return (
        f"DATABASE={settings.database};"
        f"HOSTNAME={settings.hostname};"
        f"PORT={settings.port};"
        f"PROTOCOL=TCPIP;"
        f"UID={settings.uid};"
        f"PWD={settings.pwd};"
    )


def create_connection(
    settings: DB2Settings, max_retries: int = 3, retry_delay: float = 1.0
) -> Any:
    """
    Create DB2 connection with retry logic.

    Args:
        settings: DB2 connection settings
        max_retries: Maximum number of connection attempts
        retry_delay: Initial delay between retries (seconds), exponential backoff applied

    Returns:
        DB2 connection object (ibm_db_dbi.Connection or mock)

    Raises:
        DB2ConnectionError: If connection fails after all retries
    """
    if not HAS_IBM_DB:
        log.warning("ibm_db_not_installed", message="Using mock DB2 connection")
        conn_str = build_connection_string(settings)
        return MockDB2Connection(conn_str)

    conn_str = build_connection_string(settings)
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            log.info(
                "db2_connection_attempt",
                attempt=attempt,
                max_retries=max_retries,
                hostname=settings.hostname,
                database=settings.database,
            )

            # Connect using ibm_db, then wrap with DBI for DB-API 2.0 interface
            ibm_conn = ibm_db.connect(conn_str, "", "")
            conn = ibm_db_dbi.Connection(ibm_conn)

            log.info(
                "db2_connection_success",
                hostname=settings.hostname,
                database=settings.database,
            )
            return conn

        except Exception as e:
            last_error = e
            log.warning(
                "db2_connection_failed",
                attempt=attempt,
                max_retries=max_retries,
                error=str(e),
                hostname=settings.hostname,
            )

            if attempt < max_retries:
                # Exponential backoff
                sleep_time = retry_delay * (2 ** (attempt - 1))
                log.info("retrying_connection", delay_seconds=sleep_time)
                time.sleep(sleep_time)

    # All retries exhausted
    error_msg = f"Failed to connect to DB2 after {max_retries} attempts: {last_error}"
    log.error(
        "db2_connection_exhausted",
        max_retries=max_retries,
        error=str(last_error),
        hostname=settings.hostname,
    )
    raise DB2ConnectionError(error_msg) from last_error


@contextmanager
def get_db2_connection(settings: DB2Settings) -> Generator[Any, None, None]:
    """
    Context manager for DB2 connection with automatic cleanup.

    Usage:
        with get_db2_connection(settings) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM PSRECDEFN")

    Args:
        settings: DB2 connection settings

    Yields:
        DB2 connection object

    Raises:
        DB2ConnectionError: If connection fails
    """
    conn = None
    try:
        conn = create_connection(settings)
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
                log.debug("db2_connection_closed")
            except Exception as e:
                log.warning("db2_connection_close_error", error=str(e))


@contextmanager
def get_db2_cursor(
    settings: DB2Settings, with_hold: bool = True, uncommitted_read: bool = True
) -> Generator[Any, None, None]:
    """
    Context manager for DB2 cursor with automatic connection and cursor cleanup.

    Args:
        settings: DB2 connection settings
        with_hold: Use WITH HOLD to prevent cursor closure on commit
        uncommitted_read: Use WITH UR for snapshot consistency

    Yields:
        DB2 cursor object

    Example:
        with get_db2_cursor(settings) as cursor:
            cursor.execute("SELECT * FROM PSRECDEFN WITH UR")
            rows = cursor.fetchall()
    """
    with get_db2_connection(settings) as conn:
        cursor = conn.cursor()
        try:
            # Note: WITH HOLD and WITH UR are specified in SQL, not cursor options
            # They must be included in the actual query
            yield cursor
        finally:
            try:
                cursor.close()
                log.debug("db2_cursor_closed")
            except Exception as e:
                log.warning("db2_cursor_close_error", error=str(e))


def test_connection(settings: DB2Settings) -> bool:
    """
    Test DB2 connection with a simple query.

    Args:
        settings: DB2 connection settings

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        with get_db2_cursor(settings) as cursor:
            cursor.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            result = cursor.fetchone()
            log.info("db2_connection_test_success", result=result)
            return True
    except Exception as e:
        log.error("db2_connection_test_failed", error=str(e))
        return False
