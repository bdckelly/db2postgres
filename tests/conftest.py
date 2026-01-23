"""
Pytest fixtures for PS82-to-Postgres migration tests.

Provides shared test fixtures for:
- Temporary directories and files
- Mock database connections
- Sample table definitions
- Test settings instances
"""

import tempfile
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from config.settings import DB2Settings, MigrationSettings, PostgresSettings, Settings
from src.schema.extractor import FieldDefinition, TableDefinition, ViewDefinition
from src.utils.ps_types import PSFieldType


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_db2_settings() -> DB2Settings:
    """Create test DB2 settings."""
    return DB2Settings(
        database="TEST_DB",
        hostname="localhost",
        port=50000,
        uid="testuser",
        pwd="testpass",
        use_mock=True,
    )


@pytest.fixture
def test_postgres_settings() -> PostgresSettings:
    """Create test PostgreSQL settings."""
    return PostgresSettings(
        host="localhost",
        port=5432,
        database="test_ps82",
        user="postgres",
        password="testpass",
        use_mock=True,
    )


@pytest.fixture
def test_migration_settings(temp_dir: Path) -> MigrationSettings:
    """Create test migration settings."""
    return MigrationSettings(
        min_workers=2,
        max_workers=10,
        chunk_size=1000,
        staging_dir=temp_dir / "staging",
        checkpoint_dir=temp_dir / "checkpoints",
        log_dir=temp_dir / "logs",
        log_level="DEBUG",
    )


@pytest.fixture
def test_settings(
    test_db2_settings: DB2Settings,
    test_postgres_settings: PostgresSettings,
    test_migration_settings: MigrationSettings,
) -> Settings:
    """Create complete test settings."""
    return Settings(
        db2=test_db2_settings,
        postgres=test_postgres_settings,
        migration=test_migration_settings,
    )


@pytest.fixture
def sample_table_def() -> TableDefinition:
    """Create sample table definition for PS_VOUCHER."""
    return TableDefinition(
        record_name="VOUCHER",
        sql_table_name="PS_VOUCHER",
        description="Voucher Header",
        fields=[
            FieldDefinition(
                field_name="BUSINESS_UNIT",
                field_type=PSFieldType.CHAR,
                length=10,
                is_key=True,
                key_position=1,
            ),
            FieldDefinition(
                field_name="VOUCHER_ID",
                field_type=PSFieldType.CHAR,
                length=10,
                is_key=True,
                key_position=2,
            ),
            FieldDefinition(
                field_name="INVOICE_ID",
                field_type=PSFieldType.CHAR,
                length=30,
                is_key=False,
            ),
            FieldDefinition(
                field_name="INVOICE_DT",
                field_type=PSFieldType.DATE,
                is_key=False,
            ),
            FieldDefinition(
                field_name="GROSS_AMT",
                field_type=PSFieldType.DECIMAL,
                length=28,
                decimal_places=3,
                is_key=False,
            ),
            FieldDefinition(
                field_name="DESCR",
                field_type=PSFieldType.VARCHAR,
                length=255,
                is_key=False,
            ),
        ],
        estimated_rows=5_000_000,
    )


@pytest.fixture
def sample_effdt_table_def() -> TableDefinition:
    """Create sample effective-dated table definition."""
    return TableDefinition(
        record_name="JOB",
        sql_table_name="PS_JOB",
        description="Employee Job Data",
        fields=[
            FieldDefinition(
                field_name="EMPLID",
                field_type=PSFieldType.CHAR,
                length=11,
                is_key=True,
                key_position=1,
            ),
            FieldDefinition(
                field_name="EMPL_RCD",
                field_type=PSFieldType.INTEGER,
                is_key=True,
                key_position=2,
            ),
            FieldDefinition(
                field_name="EFFDT",
                field_type=PSFieldType.DATE,
                is_key=True,
                key_position=3,
            ),
            FieldDefinition(
                field_name="EFFSEQ",
                field_type=PSFieldType.SMALLINT,
                is_key=True,
                key_position=4,
            ),
            FieldDefinition(
                field_name="JOBCODE",
                field_type=PSFieldType.CHAR,
                length=13,
                is_key=False,
            ),
            FieldDefinition(
                field_name="POSITION_NBR",
                field_type=PSFieldType.CHAR,
                length=8,
                is_key=False,
            ),
        ],
        estimated_rows=2_000_000,
    )


@pytest.fixture
def sample_small_table_def() -> TableDefinition:
    """Create sample small table definition."""
    return TableDefinition(
        record_name="XLATTABLE",
        sql_table_name="PS_XLATTABLE",
        description="Translate Table",
        fields=[
            FieldDefinition(
                field_name="FIELDNAME",
                field_type=PSFieldType.CHAR,
                length=18,
                is_key=True,
                key_position=1,
            ),
            FieldDefinition(
                field_name="FIELDVALUE",
                field_type=PSFieldType.CHAR,
                length=4,
                is_key=True,
                key_position=2,
            ),
            FieldDefinition(
                field_name="EFFDT",
                field_type=PSFieldType.DATE,
                is_key=True,
                key_position=3,
            ),
            FieldDefinition(
                field_name="XLATLONGNAME",
                field_type=PSFieldType.VARCHAR,
                length=50,
                is_key=False,
            ),
            FieldDefinition(
                field_name="XLATSHORTNAME",
                field_type=PSFieldType.VARCHAR,
                length=10,
                is_key=False,
            ),
        ],
        estimated_rows=500,
    )


@pytest.fixture
def sample_blob_table_def() -> TableDefinition:
    """Create sample table with BLOB field."""
    return TableDefinition(
        record_name="IMAGE",
        sql_table_name="PS_IMAGE",
        description="Image Attachments",
        fields=[
            FieldDefinition(
                field_name="IMAGE_ID",
                field_type=PSFieldType.CHAR,
                length=30,
                is_key=True,
                key_position=1,
            ),
            FieldDefinition(
                field_name="IMAGE_NAME",
                field_type=PSFieldType.VARCHAR,
                length=100,
                is_key=False,
            ),
            FieldDefinition(
                field_name="IMAGE_DATA",
                field_type=PSFieldType.BLOB,
                is_key=False,
            ),
            FieldDefinition(
                field_name="UPLOAD_DTTM",
                field_type=PSFieldType.TIMESTAMP,
                is_key=False,
            ),
        ],
        estimated_rows=10_000,
    )


class MockDB2Cursor:
    """Mock DB2 cursor for testing."""

    def __init__(self, results: list[tuple[Any, ...]] | None = None):
        """
        Initialize mock cursor.

        Args:
            results: List of result rows to return
        """
        self.results = results or []
        self.index = 0
        self.closed = False

    def execute(self, query: str, params: tuple = ()) -> None:
        """Mock execute method."""
        pass

    def fetchmany(self, size: int = 1) -> list[tuple[Any, ...]]:
        """Mock fetchmany method."""
        if self.index >= len(self.results):
            return []

        batch = self.results[self.index : self.index + size]
        self.index += size
        return batch

    def fetchone(self) -> tuple[Any, ...] | None:
        """Mock fetchone method."""
        if self.index >= len(self.results):
            return None

        row = self.results[self.index]
        self.index += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        """Mock fetchall method."""
        remaining = self.results[self.index :]
        self.index = len(self.results)
        return remaining

    def close(self) -> None:
        """Mock close method."""
        self.closed = True


class MockDB2Connection:
    """Mock DB2 connection for testing."""

    def __init__(self, cursor_results: list[tuple[Any, ...]] | None = None):
        """
        Initialize mock connection.

        Args:
            cursor_results: Results to return from cursor
        """
        self.cursor_results = cursor_results
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> MockDB2Cursor:
        """Create mock cursor."""
        return MockDB2Cursor(self.cursor_results)

    def commit(self) -> None:
        """Mock commit."""
        self.committed = True

    def rollback(self) -> None:
        """Mock rollback."""
        self.rolled_back = True

    def close(self) -> None:
        """Mock close."""
        self.closed = True


class MockPostgresCursor:
    """Mock PostgreSQL cursor for testing."""

    def __init__(self, results: list[tuple[Any, ...]] | None = None):
        """
        Initialize mock cursor.

        Args:
            results: List of result rows to return
        """
        self.results = results or []
        self.index = 0
        self.closed = False
        self.queries: list[str] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        """Mock execute method."""
        self.queries.append(query)

    def fetchone(self) -> tuple[Any, ...] | None:
        """Mock fetchone method."""
        if self.index >= len(self.results):
            return None

        row = self.results[self.index]
        self.index += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        """Mock fetchall method."""
        remaining = self.results[self.index :]
        self.index = len(self.results)
        return remaining

    def close(self) -> None:
        """Mock close method."""
        self.closed = True


class MockPostgresConnection:
    """Mock PostgreSQL connection for testing."""

    def __init__(self, cursor_results: list[tuple[Any, ...]] | None = None):
        """
        Initialize mock connection.

        Args:
            cursor_results: Results to return from cursor
        """
        self.cursor_results = cursor_results
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> MockPostgresCursor:
        """Create mock cursor."""
        return MockPostgresCursor(self.cursor_results)

    def commit(self) -> None:
        """Mock commit."""
        self.committed = True

    def rollback(self) -> None:
        """Mock rollback."""
        self.rolled_back = True

    def close(self) -> None:
        """Mock close."""
        self.closed = True


@pytest.fixture
def mock_db2_connection() -> MockDB2Connection:
    """Create mock DB2 connection."""
    return MockDB2Connection()


@pytest.fixture
def mock_postgres_connection() -> MockPostgresConnection:
    """Create mock PostgreSQL connection."""
    return MockPostgresConnection()


@pytest.fixture
def sample_voucher_data() -> list[tuple[Any, ...]]:
    """Create sample voucher data rows."""
    return [
        ("US001", "V0000001", "INV-2025-001", datetime(2025, 1, 15), 1250.50, "Office Supplies"),
        ("US001", "V0000002", "INV-2025-002", datetime(2025, 1, 16), 3500.00, "Equipment Purchase"),
        ("US002", "V0000001", "INV-2025-003", datetime(2025, 1, 17), 875.25, "Consulting Services"),
        ("US002", "V0000002", None, datetime(2025, 1, 18), 2100.00, "Software License"),
        ("CA001", "V0000001", "INV-2025-005", None, 450.00, "Travel Expenses"),
    ]


@pytest.fixture
def sample_job_data() -> list[tuple[Any, ...]]:
    """Create sample job (effective-dated) data rows."""
    return [
        ("E0001", 0, datetime(2020, 1, 1), 0, "MGR", "P0001"),
        ("E0001", 0, datetime(2022, 6, 1), 0, "SMGR", "P0002"),
        ("E0001", 0, datetime(2024, 1, 1), 0, "DIR", "P0003"),
        ("E0002", 0, datetime(2021, 3, 15), 0, "ENG", "P0100"),
        ("E0002", 0, datetime(2023, 3, 15), 0, "SENG", "P0101"),
    ]


@pytest.fixture
def sample_view_def() -> ViewDefinition:
    """Create sample view definition for PS_VOUCHER_VW."""
    return ViewDefinition(
        record_name="VOUCHER_VW",
        sql_view_name="PS_VOUCHER_VW",
        description="Voucher Summary View",
        db2_sql_text="SELECT BUSINESS_UNIT, VOUCHER_ID, INVOICE_ID, INVOICE_DT, GROSS_AMT FROM PS_VOUCHER WHERE VOUCHER_STATUS = 'C'",
        fields=[
            FieldDefinition(
                field_name="BUSINESS_UNIT",
                field_type=PSFieldType.CHAR,
                length=10,
                is_key=False,
            ),
            FieldDefinition(
                field_name="VOUCHER_ID",
                field_type=PSFieldType.CHAR,
                length=10,
                is_key=False,
            ),
            FieldDefinition(
                field_name="INVOICE_ID",
                field_type=PSFieldType.CHAR,
                length=30,
                is_key=False,
            ),
            FieldDefinition(
                field_name="INVOICE_DT",
                field_type=PSFieldType.DATE,
                is_key=False,
            ),
            FieldDefinition(
                field_name="GROSS_AMT",
                field_type=PSFieldType.NUMBER,
                length=28,
                decimal_pos=3,
                is_key=False,
            ),
        ],
        effdt="2024-01-01",
        record_type=1,
    )


@pytest.fixture
def sample_view_with_functions() -> ViewDefinition:
    """Create sample view with DB2 functions that need conversion."""
    return ViewDefinition(
        record_name="VENDOR_VW",
        sql_view_name="PS_VENDOR_VW",
        description="Vendor Summary View",
        db2_sql_text="SELECT VENDOR_ID, VENDOR_NAME_SHORT, VENDOR_STATUS, YEAR(ADD_DT) AS ADD_YEAR, SUBSTR(VENDOR_ID, 1, 3) AS VENDOR_PREFIX FROM PS_VENDOR WHERE VENDOR_STATUS = 'A' FETCH FIRST 100 ROWS ONLY WITH UR",
        fields=[
            FieldDefinition(
                field_name="VENDOR_ID",
                field_type=PSFieldType.CHAR,
                length=10,
                is_key=False,
            ),
            FieldDefinition(
                field_name="VENDOR_NAME_SHORT",
                field_type=PSFieldType.CHAR,
                length=40,
                is_key=False,
            ),
            FieldDefinition(
                field_name="VENDOR_STATUS",
                field_type=PSFieldType.CHAR,
                length=1,
                is_key=False,
            ),
            FieldDefinition(
                field_name="ADD_YEAR",
                field_type=PSFieldType.NUMBER,
                length=4,
                is_key=False,
            ),
        ],
        effdt="2024-01-01",
        record_type=1,
    )


@pytest.fixture
def sample_xlat_data() -> list[tuple[Any, ...]]:
    """Create sample translate table data rows."""
    return [
        ("COUNTRY", "USA", datetime(1900, 1, 1), "United States", "USA"),
        ("COUNTRY", "CAN", datetime(1900, 1, 1), "Canada", "CAN"),
        ("COUNTRY", "MEX", datetime(1900, 1, 1), "Mexico", "MEX"),
        ("STATE", "CA", datetime(1900, 1, 1), "California", "CA"),
        ("STATE", "NY", datetime(1900, 1, 1), "New York", "NY"),
    ]
