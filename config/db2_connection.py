"""
DB2 z/OS connection management with retry logic and proper resource cleanup.

Provides connection pooling and error handling for mainframe DB2 connections.
"""

import time
from contextlib import contextmanager
from typing import Any, Generator

import structlog

from config.settings import DB2Settings

# Try importing DB2 drivers in order of preference
try:
    import ibm_db
    import ibm_db_dbi

    HAS_IBM_DB = True
except ImportError:
    HAS_IBM_DB = False
    ibm_db = None
    ibm_db_dbi = None

# Try JDBC as fallback (works with DBeaver's drivers)
try:
    import jaydebeapi
    import jpype

    HAS_JAYDEBEAPI = True
except ImportError:
    HAS_JAYDEBEAPI = False
    jaydebeapi = None
    jpype = None

log = structlog.get_logger()


class JDBCConnection:
    """JDBC-based DB2 connection (works with DBeaver's drivers)."""

    def __init__(self, jdbc_conn):
        self.jdbc_conn = jdbc_conn
        self.closed = False

    def cursor(self):
        return JDBCCursor(self.jdbc_conn.cursor())

    def commit(self):
        self.jdbc_conn.commit()

    def rollback(self):
        self.jdbc_conn.rollback()

    def close(self):
        if not self.closed:
            # Commit any pending transaction before closing
            try:
                self.jdbc_conn.commit()
            except Exception:
                pass  # Ignore commit errors on close
            self.jdbc_conn.close()
            self.closed = True


class JDBCCursor:
    """Wrapper for JDBC cursor to match DB-API interface."""

    def __init__(self, jdbc_cursor):
        self.jdbc_cursor = jdbc_cursor
        self._description = None

    def execute(self, query: str, params: tuple = ()) -> None:
        if params:
            self.jdbc_cursor.execute(query, params)
        else:
            self.jdbc_cursor.execute(query)
        self._description = self.jdbc_cursor.description

    def fetchone(self) -> tuple | None:
        result = self.jdbc_cursor.fetchone()
        return tuple(result) if result else None

    def fetchmany(self, size: int = 1) -> list[tuple]:
        results = self.jdbc_cursor.fetchmany(size)
        return [tuple(row) for row in results]

    def fetchall(self) -> list[tuple]:
        try:
            results = self.jdbc_cursor.fetchall()
            return [tuple(row) for row in results]
        except Exception as e:
            # Log detailed JDBC error information
            error_str = str(e)
            log.error(
                "jdbc_fetchall_failed",
                error=error_str,
                cursor_description=str(self._description),
            )
            raise

    def close(self):
        self.jdbc_cursor.close()

    @property
    def description(self):
        return self._description


class MockDB2Connection:
    """Mock DB2 connection for development without database access."""

    def __init__(self, conn_str: str):
        self.conn_str = conn_str
        self.closed = False
        log.warning("using_mock_db2_connection", reason="No DB2 drivers available")

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

    def __init__(self):
        self._results = []
        self._description = []

    def execute(self, query: str, params: tuple = ()) -> None:
        log.debug("mock_db2_query", query=query[:100], params=params)

        # Return sample data based on query
        query_upper = query.upper()

        # PSRECDEFN query (table list)
        if "PSRECDEFN" in query_upper and "SELECT" in query_upper:
            self._results = [
                ("XLATTABLE", "PS_XLATTABLE", "Translate Table", 0),
                ("INSTALLATION", "PS_INSTALLATION", "Installation Table", 0),
                ("VOUCHER", "PS_VOUCHER", "Voucher Header", 0),
                ("VCHR_LINE", "PS_VCHR_LINE", "Voucher Line", 0),
                ("JOB", "PS_JOB", "Job Data", 0),
            ]
            self._description = [
                ("RECNAME",), ("SQLTABLENAME",), ("RECDESCR",), ("RECTYPE",)
            ]
        # PSRECFIELD query (field definitions)
        elif "PSRECFIELD" in query_upper:
            # Return fields for whichever table is being queried
            if "VOUCHER" in str(params).upper() or "VOUCHER" in query_upper:
                self._results = [
                    ("VOUCHER", "BUSINESS_UNIT", None, None, 1),
                    ("VOUCHER", "VOUCHER_ID", None, None, 2),
                    ("VOUCHER", "INVOICE_ID", None, None, 3),
                    ("VOUCHER", "INVOICE_DT", None, None, 4),
                    ("VOUCHER", "GROSS_AMT", None, None, 5),
                ]
            elif "XLATTABLE" in str(params).upper() or "XLATTABLE" in query_upper:
                self._results = [
                    ("XLATTABLE", "FIELDNAME", None, None, 1),
                    ("XLATTABLE", "FIELDVALUE", None, None, 2),
                    ("XLATTABLE", "EFFDT", None, None, 3),
                    ("XLATTABLE", "XLATLONGNAME", None, None, 4),
                ]
            else:
                self._results = []
        # PSDBFIELD query (field types)
        elif "PSDBFIELD" in query_upper:
            # Return field types for common fields
            self._results = [
                ("BUSINESS_UNIT", 0, 10, 0),
                ("VOUCHER_ID", 0, 10, 0),
                ("INVOICE_ID", 0, 30, 0),
                ("INVOICE_DT", 4, 0, 0),
                ("GROSS_AMT", 2, 28, 3),
                ("FIELDNAME", 0, 18, 0),
                ("FIELDVALUE", 0, 4, 0),
                ("EFFDT", 4, 0, 0),
                ("XLATLONGNAME", 1, 50, 0),
            ]
        # PSDBFLDLABL query (field labels)
        elif "PSDBFLDLABL" in query_upper:
            self._results = []
        # Row count query
        elif "COUNT(*)" in query_upper or "COUNT(1)" in query_upper:
            if "VOUCHER" in query_upper:
                self._results = [(5000000,)]
            elif "XLATTABLE" in query_upper:
                self._results = [(500,)]
            else:
                self._results = [(1000,)]
        # Default test query
        elif "SYSDUMMY1" in query_upper:
            self._results = [(1,)]
        else:
            self._results = []

    def fetchone(self) -> tuple | None:
        if self._results:
            return self._results.pop(0)
        return None

    def fetchmany(self, size: int = 1) -> list[tuple]:
        if not self._results:
            return []
        result = self._results[:size]
        self._results = self._results[size:]
        return result

    def fetchall(self) -> list[tuple]:
        result = self._results
        self._results = []
        return result

    def close(self):
        pass

    @property
    def description(self):
        return self._description


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

    Tries drivers in this order:
    1. Mock (if use_mock=True in settings)
    2. ibm_db (native CLI driver)
    3. jaydebeapi (JDBC driver - works with DBeaver's drivers)
    4. Mock connection (automatic fallback for development)

    Args:
        settings: DB2 connection settings
        max_retries: Maximum number of connection attempts
        retry_delay: Initial delay between retries (seconds), exponential backoff applied

    Returns:
        DB2 connection object (ibm_db_dbi.Connection, JDBC, or mock)

    Raises:
        DB2ConnectionError: If connection fails after all retries
    """
    # Force mock mode if requested
    if settings.use_mock:
        log.info("mock_mode_enabled", message="Using mock DB2 connection (DB2_USE_MOCK=true)")
        conn_str = build_connection_string(settings)
        return MockDB2Connection(conn_str)

    # Try native ibm_db first
    if HAS_IBM_DB:
        return _create_ibm_db_connection(settings, max_retries, retry_delay)

    # Try JDBC as fallback
    if HAS_JAYDEBEAPI:
        return _create_jdbc_connection(settings, max_retries, retry_delay)

    # Fall back to mock
    log.warning("no_db2_drivers", message="Using mock DB2 connection (install ibm-db or configure JDBC)")
    conn_str = build_connection_string(settings)
    return MockDB2Connection(conn_str)


def _create_ibm_db_connection(
    settings: DB2Settings, max_retries: int, retry_delay: float
) -> Any:
    """Create connection using native ibm_db driver."""
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
                driver="ibm_db",
            )

            # Connect using ibm_db, then wrap with DBI for DB-API 2.0 interface
            ibm_conn = ibm_db.connect(conn_str, "", "")
            conn = ibm_db_dbi.Connection(ibm_conn)

            log.info(
                "db2_connection_success",
                hostname=settings.hostname,
                database=settings.database,
                driver="ibm_db",
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


def _create_jdbc_connection(
    settings: DB2Settings, max_retries: int, retry_delay: float
) -> Any:
    """Create connection using JDBC driver (jaydebeapi)."""
    import os

    # Look for DB2 JDBC driver in common locations
    jdbc_driver_paths = [
        # User can set this via DB2_JDBC_DRIVER_PATH in .env
        settings.jdbc_driver_path,
        # DBeaver common locations
        os.path.expanduser("~/.dbeaver/drivers/db2/db2jcc4.jar"),
        os.path.expanduser("~/.dbeaver/drivers/maven/maven-central/com/ibm/db2/jcc/*/db2jcc*.jar"),
        # System locations
        "C:/Program Files/IBM/SQLLIB/java/db2jcc4.jar",
        "/opt/ibm/db2/java/db2jcc4.jar",
        # Download from: https://repo1.maven.org/maven2/com/ibm/db2/jcc/11.5.9.0/jcc-11.5.9.0.jar
    ]

    jdbc_driver = None
    for path in jdbc_driver_paths:
        if path and os.path.exists(path):
            jdbc_driver = path
            break

    if not jdbc_driver:
        log.error(
            "jdbc_driver_not_found",
            message="DB2 JDBC driver not found. Set DB2_JDBC_DRIVER_PATH environment variable or download from Maven Central",
            searched_paths=[p for p in jdbc_driver_paths if p],
        )
        raise DB2ConnectionError(
            "DB2 JDBC driver not found. Please:\n"
            "1. Download from: https://repo1.maven.org/maven2/com/ibm/db2/jcc/11.5.9.0/jcc-11.5.9.0.jar\n"
            "2. Set environment variable: DB2_JDBC_DRIVER_PATH=path/to/db2jcc4.jar"
        )

    jdbc_url = f"jdbc:db2://{settings.hostname}:{settings.port}/{settings.database}"
    jdbc_driver_class = "com.ibm.db2.jcc.DB2Driver"

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            log.info(
                "db2_connection_attempt",
                attempt=attempt,
                max_retries=max_retries,
                hostname=settings.hostname,
                database=settings.database,
                driver="JDBC",
                jdbc_driver_path=jdbc_driver,
            )

            # Start JVM if not already started
            if not jpype.isJVMStarted():
                jpype.startJVM(jpype.getDefaultJVMPath(), "-ea", f"-Djava.class.path={jdbc_driver}")

            # Connect using JDBC
            jdbc_conn = jaydebeapi.connect(
                jdbc_driver_class,
                jdbc_url,
                [settings.uid, settings.pwd],
                jdbc_driver,
            )

            # CRITICAL: Disable autocommit to prevent result set closure
            # JDBC autocommit=True (default) closes result sets after each statement
            try:
                jdbc_conn.jconn.setAutoCommit(False)
                log.debug("jdbc_autocommit_disabled")
            except Exception as ac_err:
                log.warning("jdbc_autocommit_setting_failed", error=str(ac_err))

            # Verify connection is working with a simple query
            try:
                test_cursor = jdbc_conn.cursor()
                test_cursor.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
                test_result = test_cursor.fetchone()
                test_cursor.close()
                log.debug("jdbc_connection_verified", test_result=str(test_result))
            except Exception as verify_err:
                log.warning("jdbc_connection_verify_failed", error=str(verify_err))

            log.info(
                "db2_connection_success",
                hostname=settings.hostname,
                database=settings.database,
                driver="JDBC",
            )

            return JDBCConnection(jdbc_conn)

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
                sleep_time = retry_delay * (2 ** (attempt - 1))
                log.info("retrying_connection", delay_seconds=sleep_time)
                time.sleep(sleep_time)

    error_msg = f"Failed to connect to DB2 via JDBC after {max_retries} attempts: {last_error}"
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
