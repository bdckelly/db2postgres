"""
Unit tests for CSV serialization.
"""

import tempfile
from datetime import date, datetime, time
from pathlib import Path

import pytest

from src.extraction.serializer import (
    PostgresCopySerializer,
    estimate_csv_size,
    get_staging_file_path,
    validate_csv_file,
)


class TestPostgresCopySerializer:
    """Test PostgreSQL COPY serializer."""

    @pytest.fixture
    def temp_file(self):
        """Create temporary CSV file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            yield Path(f.name)
            # Cleanup
            Path(f.name).unlink(missing_ok=True)

    def test_serialize_null_value(self, temp_file):
        """Test NULL value serialization."""
        serializer = PostgresCopySerializer(temp_file)
        result = serializer.serialize_value(None)
        assert result == "\\N"

    def test_serialize_string(self, temp_file):
        """Test string serialization."""
        serializer = PostgresCopySerializer(temp_file)
        result = serializer.serialize_value("Test String")
        assert result == "Test String"

    def test_serialize_string_with_special_chars(self, temp_file):
        """Test string with special characters."""
        serializer = PostgresCopySerializer(temp_file)

        # Test newline
        result = serializer.serialize_value("Line1\nLine2")
        assert "\\n" in result

        # Test tab
        result = serializer.serialize_value("Col1\tCol2")
        assert "\\t" in result

        # Test backslash
        result = serializer.serialize_value("Path\\To\\File")
        assert "\\\\" in result

    def test_serialize_date(self, temp_file):
        """Test date serialization."""
        serializer = PostgresCopySerializer(temp_file)
        test_date = date(2025, 1, 15)
        result = serializer.serialize_value(test_date)
        assert result == "2025-01-15"

    def test_serialize_datetime(self, temp_file):
        """Test datetime serialization."""
        serializer = PostgresCopySerializer(temp_file)
        test_datetime = datetime(2025, 1, 15, 14, 30, 45)
        result = serializer.serialize_value(test_datetime)
        assert result == "2025-01-15 14:30:45"

    def test_serialize_time(self, temp_file):
        """Test time serialization."""
        serializer = PostgresCopySerializer(temp_file)
        test_time = time(14, 30, 45)
        result = serializer.serialize_value(test_time)
        assert result == "14:30:45"

    def test_serialize_binary(self, temp_file):
        """Test binary data serialization."""
        serializer = PostgresCopySerializer(temp_file)
        binary_data = b"\x01\x02\x03\x04"
        result = serializer.serialize_value(binary_data)
        assert result.startswith("\\\\x")
        assert "01020304" in result

    def test_serialize_boolean(self, temp_file):
        """Test boolean serialization."""
        serializer = PostgresCopySerializer(temp_file)
        assert serializer.serialize_value(True) == "t"
        assert serializer.serialize_value(False) == "f"

    def test_serialize_row(self, temp_file):
        """Test complete row serialization."""
        serializer = PostgresCopySerializer(temp_file)
        row = ("US001", "V123456", 1000.50, date(2025, 1, 15), None)
        result = serializer.serialize_row(row)

        # Should be CSV format
        assert "US001" in result
        assert "V123456" in result
        assert "1000.5" in result
        assert "2025-01-15" in result
        assert "\\N" in result

    def test_write_row(self, temp_file):
        """Test writing single row."""
        serializer = PostgresCopySerializer(temp_file)
        row = ("TEST", 123, None)

        serializer.write_row(row)

        # Verify file contents
        with open(temp_file, "r") as f:
            content = f.read()
            assert "TEST" in content
            assert "123" in content
            assert "\\N" in content

    def test_write_multiple_rows(self, temp_file):
        """Test writing multiple rows."""
        serializer = PostgresCopySerializer(temp_file)
        rows = [
            ("Row1", 1, 100.0),
            ("Row2", 2, 200.0),
            ("Row3", 3, 300.0),
        ]

        serializer.write_rows(rows)

        # Count lines
        with open(temp_file, "r") as f:
            lines = f.readlines()
            assert len(lines) == 3
            assert serializer.rows_written == 3

    def test_clear_file(self, temp_file):
        """Test clearing output file."""
        serializer = PostgresCopySerializer(temp_file)

        # Write some data
        serializer.write_row(("TEST", 123))
        assert temp_file.exists()
        assert serializer.rows_written == 1

        # Clear
        serializer.clear_file()
        assert not temp_file.exists()
        assert serializer.rows_written == 0


class TestUtilityFunctions:
    """Test utility functions."""

    def test_get_staging_file_path_no_chunk(self):
        """Test staging file path without chunk ID."""
        path = get_staging_file_path(Path("/data/staging"), "PS_VOUCHER", None)
        assert path == Path("/data/staging/PS_VOUCHER/data.csv")

    def test_get_staging_file_path_with_chunk(self):
        """Test staging file path with chunk ID."""
        path = get_staging_file_path(Path("/data/staging"), "PS_VOUCHER", 5)
        assert path == Path("/data/staging/PS_VOUCHER/chunk_005.csv")

    def test_estimate_csv_size(self):
        """Test CSV size estimation."""
        size = estimate_csv_size(row_count=1000, avg_row_size=100)
        assert size == 100_000

    def test_validate_csv_file_not_exists(self):
        """Test validation of nonexistent file."""
        is_valid, message = validate_csv_file(Path("/nonexistent/file.csv"))
        assert is_valid is False
        assert "not exist" in message.lower()

    def test_validate_csv_file_exists(self):
        """Test validation of existing file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("row1\n")
            f.write("row2\n")
            f.write("row3\n")
            temp_path = Path(f.name)

        try:
            is_valid, message = validate_csv_file(temp_path, expected_rows=3)
            assert is_valid is True
            assert "3 rows" in message
        finally:
            temp_path.unlink()
