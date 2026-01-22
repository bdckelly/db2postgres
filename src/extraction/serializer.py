"""
Data serialization for PostgreSQL COPY.

Converts DB2 rows to PostgreSQL COPY-compatible CSV format with proper:
- NULL handling (\\N)
- Escaping of special characters
- UTF-8 encoding
- Binary data handling
- Streaming writes
"""

import csv
from datetime import date, datetime, time
from io import StringIO
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


class PostgresCopySerializer:
    """
    Serialize DB2 rows to PostgreSQL COPY-compatible CSV format.

    PostgreSQL COPY format specifications:
    - NULL values represented as \\N
    - Text format with delimiters
    - Escape special characters
    - UTF-8 encoding
    """

    def __init__(
        self,
        output_file: Path,
        delimiter: str = ",",
        null_string: str = "\\N",
    ):
        """
        Initialize CSV serializer for PostgreSQL COPY.

        Args:
            output_file: Output CSV file path
            delimiter: Field delimiter (default: comma)
            null_string: String to represent NULL values (default: \\N for PostgreSQL)
        """
        self.output_file = output_file
        self.delimiter = delimiter
        self.null_string = null_string
        self.rows_written = 0

        # Ensure output directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        log.info("serializer_initialized", output_file=str(output_file))

    def serialize_value(self, value: Any) -> str:
        """
        Serialize a single value to PostgreSQL COPY format.

        Args:
            value: Value to serialize

        Returns:
            str: Serialized value
        """
        # Handle NULL
        if value is None:
            return self.null_string

        # Handle dates and times
        if isinstance(value, datetime):
            # Format: YYYY-MM-DD HH:MM:SS
            return value.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, date):
            # Format: YYYY-MM-DD
            return value.strftime("%Y-%m-%d")
        elif isinstance(value, time):
            # Format: HH:MM:SS
            return value.strftime("%H:%M:%S")

        # Handle binary data (convert to hex)
        if isinstance(value, (bytes, bytearray)):
            # PostgreSQL BYTEA format: \\x followed by hex
            return "\\\\x" + value.hex()

        # Handle boolean
        if isinstance(value, bool):
            return "t" if value else "f"

        # Convert to string and escape special characters
        str_value = str(value)

        # Escape backslashes and delimiters for CSV
        # PostgreSQL COPY uses backslash escaping
        str_value = str_value.replace("\\", "\\\\")
        str_value = str_value.replace("\n", "\\n")
        str_value = str_value.replace("\r", "\\r")
        str_value = str_value.replace("\t", "\\t")

        return str_value

    def serialize_row(self, row: tuple[Any, ...]) -> str:
        """
        Serialize a complete row to CSV format.

        Args:
            row: Tuple of values from DB2

        Returns:
            str: CSV line with proper escaping and NULL handling
        """
        serialized_values = [self.serialize_value(val) for val in row]

        # Use Python's CSV writer for proper quoting
        output = StringIO()
        writer = csv.writer(
            output,
            delimiter=self.delimiter,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="",
        )
        writer.writerow(serialized_values)

        return output.getvalue()

    def write_row(self, row: tuple[Any, ...]) -> None:
        """
        Write a single row to output file.

        Args:
            row: Tuple of values from DB2
        """
        csv_line = self.serialize_row(row)

        with open(self.output_file, "a", encoding="utf-8", newline="") as f:
            f.write(csv_line)
            f.write("\n")

        self.rows_written += 1

    def write_rows(self, rows: list[tuple[Any, ...]]) -> None:
        """
        Write multiple rows to output file (more efficient than individual writes).

        Args:
            rows: List of row tuples from DB2
        """
        if not rows:
            return

        with open(self.output_file, "a", encoding="utf-8", newline="") as f:
            for row in rows:
                csv_line = self.serialize_row(row)
                f.write(csv_line)
                f.write("\n")

        self.rows_written += len(rows)

        log.debug(
            "rows_written",
            output_file=str(self.output_file),
            batch_size=len(rows),
            total_rows=self.rows_written,
        )

    def write_streaming(self, row_generator) -> int:
        """
        Write rows from generator (streaming, memory-efficient).

        Args:
            row_generator: Generator yielding row tuples

        Returns:
            int: Number of rows written
        """
        log.info("streaming_write_started", output_file=str(self.output_file))

        batch = []
        batch_size = 1000

        for row in row_generator:
            batch.append(row)

            if len(batch) >= batch_size:
                self.write_rows(batch)
                batch = []

        # Write remaining rows
        if batch:
            self.write_rows(batch)

        log.info(
            "streaming_write_completed",
            output_file=str(self.output_file),
            total_rows=self.rows_written,
        )

        return self.rows_written

    def clear_file(self) -> None:
        """Clear output file (for restarting extraction)."""
        if self.output_file.exists():
            self.output_file.unlink()
            self.rows_written = 0
            log.info("output_file_cleared", output_file=str(self.output_file))


class BinaryFileSerializer:
    """
    Serialize large binary objects (BLOBs) to separate files.

    For PSIMAGE and other large binary fields, store separately
    to avoid bloating CSV files.
    """

    def __init__(self, output_dir: Path, table_name: str):
        """
        Initialize binary file serializer.

        Args:
            output_dir: Directory for binary files
            table_name: Table name (for organizing files)
        """
        self.output_dir = output_dir / table_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.files_written = 0

        log.info("binary_serializer_initialized", output_dir=str(self.output_dir))

    def write_blob(self, blob_data: bytes, key: str, field_name: str) -> str:
        """
        Write BLOB data to file.

        Args:
            blob_data: Binary data
            key: Unique key for this row (e.g., primary key values)
            field_name: Field name

        Returns:
            str: Path to written file (relative reference for tracking)
        """
        # Create filename from key and field name
        safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        filename = f"{safe_key}_{field_name}.bin"
        file_path = self.output_dir / filename

        with open(file_path, "wb") as f:
            f.write(blob_data)

        self.files_written += 1

        log.debug("blob_written", file=str(file_path), size=len(blob_data))

        return str(file_path)


def get_staging_file_path(
    staging_dir: Path, table_name: str, chunk_id: int | None = None
) -> Path:
    """
    Get staging file path for table/chunk.

    Args:
        staging_dir: Staging directory root
        table_name: Table name
        chunk_id: Optional chunk ID for chunked extraction

    Returns:
        Path: Staging file path

    Example:
        >>> path = get_staging_file_path(Path("data/staging"), "PS_VOUCHER", 5)
        >>> print(path)
        data/staging/PS_VOUCHER/chunk_005.csv
    """
    table_dir = staging_dir / table_name
    table_dir.mkdir(parents=True, exist_ok=True)

    if chunk_id is not None:
        filename = f"chunk_{chunk_id:03d}.csv"
    else:
        filename = "data.csv"

    return table_dir / filename


def estimate_csv_size(row_count: int, avg_row_size: int = 100) -> int:
    """
    Estimate CSV file size in bytes.

    Args:
        row_count: Number of rows
        avg_row_size: Estimated average row size in bytes

    Returns:
        int: Estimated file size in bytes
    """
    return row_count * avg_row_size


def validate_csv_file(csv_file: Path, expected_rows: int | None = None) -> tuple[bool, str]:
    """
    Validate CSV file was written correctly.

    Args:
        csv_file: Path to CSV file
        expected_rows: Expected row count (optional)

    Returns:
        tuple: (is_valid, message)
    """
    if not csv_file.exists():
        return False, "File does not exist"

    try:
        # Count lines in file
        with open(csv_file, "r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

        if expected_rows and line_count != expected_rows:
            return False, f"Row count mismatch: expected {expected_rows}, got {line_count}"

        return True, f"Valid CSV with {line_count} rows"

    except Exception as e:
        return False, f"Validation error: {e}"
