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


def start_ssh_tunnel(ssh_settings: SSHTunnelSettings, pg_port: int, force_new: bool = False) -> Any:
    """
    Start SSH tunnel for PostgreSQL connection.

    Args:
        ssh_settings: SSH tunnel configuration
        pg_port: PostgreSQL port to forward
        force_new: If True, close existing tunnel and create a new one

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

    # Close existing tunnel if force_new requested
    if force_new and _ssh_tunnel is not None:
        try:
            _ssh_tunnel.stop()
        except Exception:
            pass
        _ssh_tunnel = None

    # Return existing tunnel if already running and healthy
    if _ssh_tunnel is not None and _ssh_tunnel.is_active:
        # Verify tunnel is actually working by checking transport
        try:
            transport = _ssh_tunnel.ssh_transport
            if transport and transport.is_active():
                log.debug("ssh_tunnel_already_active", local_port=_ssh_tunnel.local_bind_port)
                return _ssh_tunnel
            else:
                log.warning("ssh_tunnel_transport_inactive", message="Recreating tunnel")
                try:
                    _ssh_tunnel.stop()
                except Exception:
                    pass
                _ssh_tunnel = None
        except Exception as e:
            log.warning("ssh_tunnel_health_check_failed", error=str(e), message="Recreating tunnel")
            try:
                _ssh_tunnel.stop()
            except Exception:
                pass
            _ssh_tunnel = None

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

        # Create tunnel with keepalive to prevent session timeouts
        _ssh_tunnel = SSHTunnelForwarder(
            (ssh_settings.host, ssh_settings.port),
            ssh_username=ssh_settings.user,
            remote_bind_address=("localhost", pg_port),
            set_keepalive=30.0,  # Send keepalive every 30 seconds
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

    If connection through an existing tunnel fails, automatically retries
    with a fresh tunnel.

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

    # Determine if using SSH tunnel
    use_tunnel = ssh_settings and ssh_settings.tunnel_enabled
    max_attempts = 2 if use_tunnel else 1  # Retry once with fresh tunnel if tunnel fails

    last_error = None
    for attempt in range(max_attempts):
        # Start SSH tunnel if enabled
        tunnel = None
        connect_host = settings.host
        connect_port = settings.port

        if use_tunnel:
            # On retry, force a new tunnel
            force_new = attempt > 0
            if force_new:
                log.info("ssh_tunnel_retry", message="Retrying with fresh SSH tunnel")
            tunnel = start_ssh_tunnel(ssh_settings, settings.port, force_new=force_new)
            connect_host = "127.0.0.1"  # Connect to local tunnel endpoint
            connect_port = tunnel.local_bind_port
            log.debug(
                "postgres_using_ssh_tunnel",
                tunnel_local_port=connect_port,
                remote_host=settings.host,
                remote_port=settings.port,
            )

        try:
            conninfo = settings.connection_string(host=connect_host, port=connect_port)
            log.debug(
                "postgres_connection_attempt",
                host=connect_host,
                port=connect_port,
                database=settings.database,
                via_ssh_tunnel=tunnel is not None,
            )

            # Add connection timeout (10 seconds) to avoid hanging forever
            conn = psycopg.connect(conninfo, connect_timeout=10)

            log.debug(
                "postgres_connection_success",
                host=settings.host,
                database=settings.database,
                via_ssh_tunnel=tunnel is not None,
            )
            return conn

        except Exception as e:
            last_error = e
            error_msg = str(e)

            # Check if this looks like a tunnel failure that might be recoverable
            tunnel_error_indicators = [
                "SSH session not active",
                "server closed the connection unexpectedly",
                "connection refused",
                "could not receive data from server",
            ]
            is_tunnel_error = use_tunnel and any(
                indicator.lower() in error_msg.lower() for indicator in tunnel_error_indicators
            )

            if is_tunnel_error and attempt < max_attempts - 1:
                log.warning(
                    "postgres_connection_tunnel_error",
                    error=error_msg,
                    message="Will retry with fresh tunnel",
                )
                continue

            log.error(
                "postgres_connection_failed",
                error=error_msg,
                host=connect_host,
                port=connect_port,
                database=settings.database,
                via_ssh_tunnel=tunnel is not None,
            )
            raise PostgresConnectionError(f"Failed to connect to PostgreSQL: {e}") from e

    # Should not reach here, but just in case
    raise PostgresConnectionError(f"Failed to connect to PostgreSQL: {last_error}") from last_error


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
