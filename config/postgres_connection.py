"""
PostgreSQL connection management for bulk loading and validation.

Provides connection pooling and optimized settings for COPY operations.
Supports SSH tunneling for secure remote connections.
"""

from contextlib import contextmanager
from typing import Any, Generator

import structlog

from config.settings import PostgresSettings, SSHTunnelSettings

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

# SSH tunnel support
# Compatibility shim for sshtunnel with paramiko 3.0+
# paramiko 3.0 removed DSSKey (DSS/DSA deprecated), but sshtunnel 0.4.0 still references it
try:
    import paramiko

    if not hasattr(paramiko, "DSSKey"):
        # Create a dummy DSSKey class that will never match any key type
        # This allows sshtunnel to import without errors
        class _DummyDSSKey:
            """Dummy class to satisfy sshtunnel's reference to removed paramiko.DSSKey"""

            pass

        paramiko.DSSKey = _DummyDSSKey
except ImportError:
    pass

try:
    from sshtunnel import SSHTunnelForwarder

    HAS_SSHTUNNEL = True
except ImportError:
    HAS_SSHTUNNEL = False
    SSHTunnelForwarder = None

log = structlog.get_logger()

# Global SSH tunnel instance (for connection reuse)
_ssh_tunnel: Any = None


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


def start_ssh_tunnel(ssh_settings: SSHTunnelSettings, pg_port: int) -> Any:
    """
    Start SSH tunnel for PostgreSQL connection.

    Args:
        ssh_settings: SSH tunnel configuration
        pg_port: PostgreSQL port to forward

    Returns:
        SSHTunnelForwarder instance

    Raises:
        PostgresConnectionError: If tunnel setup fails
    """
    global _ssh_tunnel

    if not HAS_SSHTUNNEL:
        raise PostgresConnectionError(
            "SSH tunnel requested but sshtunnel package not installed. "
            "Install with: pip install sshtunnel"
        )

    # Return existing tunnel if already running
    if _ssh_tunnel is not None and _ssh_tunnel.is_active:
        log.info("ssh_tunnel_already_active", local_port=_ssh_tunnel.local_bind_port)
        return _ssh_tunnel

    try:
        log.info(
            "ssh_tunnel_starting",
            ssh_host=ssh_settings.host,
            ssh_port=ssh_settings.port,
            ssh_user=ssh_settings.user,
            remote_port=pg_port,
        )

        # Configure SSH authentication
        ssh_kwargs = {}
        if ssh_settings.key_file:
            ssh_kwargs["ssh_pkey"] = ssh_settings.key_file
        elif ssh_settings.password:
            ssh_kwargs["ssh_password"] = ssh_settings.password
        else:
            raise PostgresConnectionError(
                "SSH tunnel enabled but no authentication method configured. "
                "Set SSH_PASSWORD or SSH_KEY_FILE in .env"
            )

        # Create tunnel
        _ssh_tunnel = SSHTunnelForwarder(
            (ssh_settings.host, ssh_settings.port),
            ssh_username=ssh_settings.user,
            remote_bind_address=("localhost", pg_port),
            **ssh_kwargs,
        )

        _ssh_tunnel.start()

        log.info(
            "ssh_tunnel_started",
            ssh_host=ssh_settings.host,
            local_port=_ssh_tunnel.local_bind_port,
            remote_port=pg_port,
        )

        return _ssh_tunnel

    except Exception as e:
        error_msg = f"Failed to start SSH tunnel: {e}"
        log.error("ssh_tunnel_failed", error=str(e))
        raise PostgresConnectionError(error_msg) from e


def stop_ssh_tunnel() -> None:
    """Stop active SSH tunnel."""
    global _ssh_tunnel

    if _ssh_tunnel is not None and _ssh_tunnel.is_active:
        try:
            _ssh_tunnel.stop()
            log.info("ssh_tunnel_stopped")
        except Exception as e:
            log.warning("ssh_tunnel_stop_error", error=str(e))
        finally:
            _ssh_tunnel = None


def create_connection(
    settings: PostgresSettings, ssh_settings: SSHTunnelSettings | None = None
) -> Any:
    """
    Create PostgreSQL connection, optionally through SSH tunnel.

    Args:
        settings: PostgreSQL connection settings
        ssh_settings: SSH tunnel settings (optional)

    Returns:
        PostgreSQL connection object (psycopg.Connection or mock)

    Raises:
        PostgresConnectionError: If connection fails
    """
    if not HAS_PSYCOPG:
        log.warning("psycopg_not_installed", message="Using mock PostgreSQL connection")
        conninfo = settings.connection_string()
        return MockPostgresConnection(conninfo)

    # Start SSH tunnel if enabled
    tunnel = None
    connect_host = settings.host
    connect_port = settings.port

    if ssh_settings and ssh_settings.tunnel_enabled:
        tunnel = start_ssh_tunnel(ssh_settings, settings.port)
        connect_host = "127.0.0.1"  # Connect to local tunnel endpoint
        connect_port = tunnel.local_bind_port
        log.info(
            "postgres_using_ssh_tunnel",
            tunnel_local_port=connect_port,
            remote_host=settings.host,
            remote_port=settings.port,
        )

    try:
        conninfo = settings.connection_string(host=connect_host, port=connect_port)
        log.info(
            "postgres_connection_attempt",
            host=connect_host,
            port=connect_port,
            database=settings.database,
            via_ssh_tunnel=tunnel is not None,
        )

        # Add connection timeout (10 seconds) to avoid hanging forever
        conn = psycopg.connect(conninfo, connect_timeout=10)

        log.info(
            "postgres_connection_success",
            host=settings.host,
            database=settings.database,
            via_ssh_tunnel=tunnel is not None,
        )
        return conn

    except Exception as e:
        error_msg = f"Failed to connect to PostgreSQL: {e}"
        log.error(
            "postgres_connection_failed",
            error=str(e),
            host=connect_host,
            port=connect_port,
            database=settings.database,
            via_ssh_tunnel=tunnel is not None,
        )
        raise PostgresConnectionError(error_msg) from e


@contextmanager
def get_postgres_connection(
    settings: PostgresSettings, ssh_settings: SSHTunnelSettings | None = None
) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL connection with automatic cleanup.

    Usage:
        with get_postgres_connection(settings, ssh_settings) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ps_voucher")

    Args:
        settings: PostgreSQL connection settings
        ssh_settings: SSH tunnel settings (optional)

    Yields:
        PostgreSQL connection object

    Raises:
        PostgresConnectionError: If connection fails
    """
    conn = None
    try:
        conn = create_connection(settings, ssh_settings)
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
                log.debug("postgres_connection_closed")
            except Exception as e:
                log.warning("postgres_connection_close_error", error=str(e))


@contextmanager
def get_postgres_cursor(
    settings: PostgresSettings, ssh_settings: SSHTunnelSettings | None = None
) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL cursor with automatic connection and cursor cleanup.

    Args:
        settings: PostgreSQL connection settings
        ssh_settings: SSH tunnel settings (optional)

    Yields:
        PostgreSQL cursor object

    Example:
        with get_postgres_cursor(settings, ssh_settings) as cursor:
            cursor.execute("SELECT COUNT(*) FROM ps_voucher")
            count = cursor.fetchone()[0]
    """
    with get_postgres_connection(settings, ssh_settings) as conn:
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
    settings: PostgresSettings,
    ssh_settings: SSHTunnelSettings | None = None,
    table_name: str | None = None,
) -> Generator[Any, None, None]:
    """
    Context manager for PostgreSQL connection optimized for bulk loading.

    Applies optimizations for COPY operations:
    - Increases work_mem for better performance
    - Can disable triggers if needed
    - Optimized for write-heavy workloads

    Args:
        settings: PostgreSQL connection settings
        ssh_settings: SSH tunnel settings (optional)
        table_name: Optional table name for table-specific optimizations

    Yields:
        PostgreSQL connection object configured for bulk loading

    Example:
        with get_bulk_load_connection(settings, ssh_settings, "ps_voucher") as conn:
            cursor = conn.cursor()
            with cursor.copy("COPY ps_voucher FROM STDIN WITH (FORMAT CSV)") as copy:
                for line in csv_file:
                    copy.write(line)
            conn.commit()
    """
    with get_postgres_connection(settings, ssh_settings) as conn:
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


def test_connection(
    settings: PostgresSettings, ssh_settings: SSHTunnelSettings | None = None
) -> bool:
    """
    Test PostgreSQL connection with a simple query.

    Args:
        settings: PostgreSQL connection settings
        ssh_settings: SSH tunnel settings (optional)

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        with get_postgres_cursor(settings, ssh_settings) as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            log.info("postgres_connection_test_success", result=result)
            return True
    except Exception as e:
        log.error("postgres_connection_test_failed", error=str(e))
        return False


def get_table_row_count(
    settings: PostgresSettings, table_name: str, ssh_settings: SSHTunnelSettings | None = None
) -> int:
    """
    Get row count for a PostgreSQL table.

    Args:
        settings: PostgreSQL connection settings
        table_name: Name of the table (lowercase, e.g. 'ps_voucher')
        ssh_settings: SSH tunnel settings (optional)

    Returns:
        int: Number of rows in the table

    Raises:
        PostgresQueryError: If query fails
    """
    try:
        with get_postgres_cursor(settings, ssh_settings) as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            result = cursor.fetchone()
            count = result[0] if result else 0
            log.info("postgres_row_count", table=table_name, count=count)
            return count
    except Exception as e:
        error_msg = f"Failed to get row count for {table_name}: {e}"
        log.error("postgres_row_count_failed", table=table_name, error=str(e))
        raise PostgresQueryError(error_msg) from e
