"""
Table chunking strategies for large table extraction.

Implements multiple strategies for partitioning large tables into manageable chunks:
- DateChunkStrategy: For tables with EFFDT or date fields
- NumericRangeStrategy: For tables with numeric keys
- OffsetLimitStrategy: Fallback for tables without good chunking keys
- NoChunkStrategy: For small tables
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

import structlog

from config.db2_connection import get_db2_cursor
from config.settings import DB2Settings
from src.schema.extractor import TableDefinition
from src.utils.ps_types import suggest_chunk_key

log = structlog.get_logger()


class TableSize(str, Enum):
    """Table size categories for chunking decisions."""

    TINY = "tiny"  # < 1,000 rows
    SMALL = "small"  # < 100,000 rows
    MEDIUM = "medium"  # < 10,000,000 rows
    LARGE = "large"  # >= 10,000,000 rows


@dataclass
class ChunkDefinition:
    """Definition of a single chunk for extraction."""

    chunk_id: int
    where_clause: str
    estimated_rows: int | None = None
    params: tuple[Any, ...] = ()

    def __repr__(self) -> str:
        return f"Chunk({self.chunk_id}, rows≈{self.estimated_rows}, where={self.where_clause[:50]}...)"


class ChunkStrategy(ABC):
    """Abstract base class for chunking strategies."""

    def __init__(self, table_def: TableDefinition, db2_settings: DB2Settings, chunk_size: int):
        """
        Initialize chunking strategy.

        Args:
            table_def: Table definition
            db2_settings: DB2 connection settings
            chunk_size: Target rows per chunk
        """
        self.table_def = table_def
        self.db2_settings = db2_settings
        self.chunk_size = chunk_size
        self.table_name = table_def.sql_table_name

    @abstractmethod
    def generate_chunks(self) -> list[ChunkDefinition]:
        """
        Generate chunk definitions for this table.

        Returns:
            list[ChunkDefinition]: List of chunks to extract
        """
        pass

    def estimate_row_count(self) -> int:
        """
        Estimate total row count for table.

        Returns:
            int: Estimated row count
        """
        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                # Use CARDINALITY from catalog for fast estimate
                query = f"""
                    SELECT CARD
                    FROM SYSIBM.SYSTABLES
                    WHERE NAME = '{self.table_name}'
                    WITH UR
                """
                cursor.execute(query)
                row = cursor.fetchone()
                if row and row[0]:
                    count = int(row[0])
                    log.debug("row_count_estimated", table=self.table_name, count=count)
                    return count

                # Fallback to COUNT(*) if catalog doesn't have stats
                query = f"SELECT COUNT(*) FROM {self.table_name} WITH UR"
                cursor.execute(query)
                row = cursor.fetchone()
                count = row[0] if row else 0
                log.debug("row_count_counted", table=self.table_name, count=count)
                return count

        except Exception as e:
            log.warning("row_count_estimation_failed", table=self.table_name, error=str(e))
            return 0


class NoChunkStrategy(ChunkStrategy):
    """No chunking - extract entire table in one query."""

    def generate_chunks(self) -> list[ChunkDefinition]:
        """Generate single chunk for entire table."""
        log.info("no_chunking", table=self.table_name)
        return [
            ChunkDefinition(
                chunk_id=0,
                where_clause="1=1",
                estimated_rows=self.estimate_row_count(),
            )
        ]


class DateChunkStrategy(ChunkStrategy):
    """Chunk by date ranges (optimized for EFFDT fields)."""

    def __init__(
        self,
        table_def: TableDefinition,
        db2_settings: DB2Settings,
        chunk_size: int,
        date_column: str = "EFFDT",
    ):
        """
        Initialize date-based chunking.

        Args:
            table_def: Table definition
            db2_settings: DB2 settings
            chunk_size: Target rows per chunk
            date_column: Name of date column to chunk by
        """
        super().__init__(table_def, db2_settings, chunk_size)
        self.date_column = date_column

    def get_date_range(self) -> tuple[date | None, date | None]:
        """
        Get min and max dates from table.

        Returns:
            tuple: (min_date, max_date)
        """
        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                query = f"""
                    SELECT MIN({self.date_column}), MAX({self.date_column})
                    FROM {self.table_name}
                    WITH UR
                """
                cursor.execute(query)
                row = cursor.fetchone()

                if row and row[0] and row[1]:
                    min_date = row[0]
                    max_date = row[1]
                    log.debug(
                        "date_range_retrieved",
                        table=self.table_name,
                        min_date=min_date,
                        max_date=max_date,
                    )
                    return min_date, max_date

                return None, None

        except Exception as e:
            log.error("date_range_retrieval_failed", table=self.table_name, error=str(e))
            return None, None

    def generate_chunks(self) -> list[ChunkDefinition]:
        """Generate chunks by date ranges."""
        log.info("date_chunking", table=self.table_name, date_column=self.date_column)

        min_date, max_date = self.get_date_range()
        if not min_date or not max_date:
            log.warning("no_date_range_falling_back", table=self.table_name)
            # Fallback to no chunking
            return NoChunkStrategy(self.table_def, self.db2_settings, self.chunk_size).generate_chunks()

        # Calculate number of chunks needed
        total_rows = self.estimate_row_count()
        if total_rows == 0:
            return []

        num_chunks = max(1, (total_rows + self.chunk_size - 1) // self.chunk_size)

        # Calculate date range per chunk
        total_days = (max_date - min_date).days + 1
        days_per_chunk = max(1, total_days // num_chunks)

        chunks = []
        current_date = min_date
        chunk_id = 0

        while current_date <= max_date:
            next_date = current_date + timedelta(days=days_per_chunk)

            if next_date > max_date:
                # Last chunk - include everything up to max_date
                where_clause = f"{self.date_column} >= ? AND {self.date_column} <= ?"
                params = (current_date, max_date)
            else:
                where_clause = f"{self.date_column} >= ? AND {self.date_column} < ?"
                params = (current_date, next_date)

            chunks.append(
                ChunkDefinition(
                    chunk_id=chunk_id,
                    where_clause=where_clause,
                    estimated_rows=self.chunk_size,
                    params=params,
                )
            )

            current_date = next_date
            chunk_id += 1

            # Safety check to avoid infinite loop
            if chunk_id > 10000:
                log.error("too_many_chunks", table=self.table_name, chunk_id=chunk_id)
                break

        log.info(
            "date_chunks_generated",
            table=self.table_name,
            chunks=len(chunks),
            date_range=(min_date, max_date),
        )
        return chunks


class NumericRangeStrategy(ChunkStrategy):
    """Chunk by numeric key ranges."""

    def __init__(
        self,
        table_def: TableDefinition,
        db2_settings: DB2Settings,
        chunk_size: int,
        key_column: str,
    ):
        """
        Initialize numeric range chunking.

        Args:
            table_def: Table definition
            db2_settings: DB2 settings
            chunk_size: Target rows per chunk
            key_column: Name of numeric key column
        """
        super().__init__(table_def, db2_settings, chunk_size)
        self.key_column = key_column

    def get_numeric_range(self) -> tuple[int | None, int | None]:
        """
        Get min and max values for numeric key.

        Returns:
            tuple: (min_value, max_value)
        """
        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                query = f"""
                    SELECT MIN({self.key_column}), MAX({self.key_column})
                    FROM {self.table_name}
                    WITH UR
                """
                cursor.execute(query)
                row = cursor.fetchone()

                if row and row[0] is not None and row[1] is not None:
                    min_val = int(row[0])
                    max_val = int(row[1])
                    log.debug(
                        "numeric_range_retrieved",
                        table=self.table_name,
                        min_val=min_val,
                        max_val=max_val,
                    )
                    return min_val, max_val

                return None, None

        except Exception as e:
            log.error("numeric_range_retrieval_failed", table=self.table_name, error=str(e))
            return None, None

    def generate_chunks(self) -> list[ChunkDefinition]:
        """Generate chunks by numeric ranges."""
        log.info("numeric_chunking", table=self.table_name, key_column=self.key_column)

        min_val, max_val = self.get_numeric_range()
        if min_val is None or max_val is None:
            log.warning("no_numeric_range_falling_back", table=self.table_name)
            return NoChunkStrategy(self.table_def, self.db2_settings, self.chunk_size).generate_chunks()

        # Calculate number of chunks needed
        total_rows = self.estimate_row_count()
        if total_rows == 0:
            return []

        num_chunks = max(1, (total_rows + self.chunk_size - 1) // self.chunk_size)

        # Calculate range per chunk
        total_range = max_val - min_val + 1
        range_per_chunk = max(1, total_range // num_chunks)

        chunks = []
        current_val = min_val
        chunk_id = 0

        while current_val <= max_val:
            next_val = current_val + range_per_chunk

            if next_val > max_val:
                # Last chunk
                where_clause = f"{self.key_column} >= ? AND {self.key_column} <= ?"
                params = (current_val, max_val)
            else:
                where_clause = f"{self.key_column} >= ? AND {self.key_column} < ?"
                params = (current_val, next_val)

            chunks.append(
                ChunkDefinition(
                    chunk_id=chunk_id,
                    where_clause=where_clause,
                    estimated_rows=self.chunk_size,
                    params=params,
                )
            )

            current_val = next_val
            chunk_id += 1

            # Safety check
            if chunk_id > 10000:
                log.error("too_many_chunks", table=self.table_name, chunk_id=chunk_id)
                break

        log.info(
            "numeric_chunks_generated",
            table=self.table_name,
            chunks=len(chunks),
            range=(min_val, max_val),
        )
        return chunks


class OffsetLimitStrategy(ChunkStrategy):
    """Chunk using OFFSET/LIMIT (fallback strategy)."""

    def generate_chunks(self) -> list[ChunkDefinition]:
        """Generate chunks using offset/limit."""
        log.info("offset_limit_chunking", table=self.table_name)

        total_rows = self.estimate_row_count()
        if total_rows == 0:
            return []

        num_chunks = max(1, (total_rows + self.chunk_size - 1) // self.chunk_size)

        chunks = []
        for chunk_id in range(num_chunks):
            offset = chunk_id * self.chunk_size
            # DB2 uses FETCH FIRST n ROWS ONLY instead of LIMIT
            where_clause = f"1=1 OFFSET {offset} ROWS FETCH FIRST {self.chunk_size} ROWS ONLY"

            chunks.append(
                ChunkDefinition(
                    chunk_id=chunk_id,
                    where_clause=where_clause,
                    estimated_rows=min(self.chunk_size, total_rows - offset),
                )
            )

        log.info("offset_limit_chunks_generated", table=self.table_name, chunks=len(chunks))
        return chunks


def determine_table_size(row_count: int) -> TableSize:
    """
    Determine table size category from row count.

    Args:
        row_count: Number of rows in table

    Returns:
        TableSize: Size category
    """
    if row_count < 1_000:
        return TableSize.TINY
    elif row_count < 100_000:
        return TableSize.SMALL
    elif row_count < 10_000_000:
        return TableSize.MEDIUM
    else:
        return TableSize.LARGE


def determine_chunking_strategy(
    table_def: TableDefinition,
    db2_settings: DB2Settings,
    chunk_size: int = 500_000,
) -> ChunkStrategy:
    """
    Determine best chunking strategy for table.

    Args:
        table_def: Table definition
        db2_settings: DB2 settings
        chunk_size: Target rows per chunk

    Returns:
        ChunkStrategy: Appropriate chunking strategy

    Example:
        >>> strategy = determine_chunking_strategy(table_def, db2_settings)
        >>> chunks = strategy.generate_chunks()
        >>> for chunk in chunks:
        ...     print(f"Chunk {chunk.chunk_id}: {chunk.where_clause}")
    """
    log.info("determining_chunking_strategy", table=table_def.sql_table_name)

    # Create a temporary strategy to estimate row count
    temp_strategy = NoChunkStrategy(table_def, db2_settings, chunk_size)
    row_count = temp_strategy.estimate_row_count()

    table_size = determine_table_size(row_count)

    log.info(
        "table_size_determined",
        table=table_def.sql_table_name,
        size=table_size.value,
        rows=row_count,
    )

    # Tiny and small tables don't need chunking
    if table_size in (TableSize.TINY, TableSize.SMALL):
        log.info("using_no_chunk_strategy", table=table_def.sql_table_name)
        return NoChunkStrategy(table_def, db2_settings, chunk_size)

    # For medium/large tables, determine best chunking approach
    key_fields = table_def.key_fields

    # Check for EFFDT (preferred for date-based chunking)
    if "EFFDT" in [f.upper() for f in key_fields]:
        log.info("using_date_chunk_strategy", table=table_def.sql_table_name, column="EFFDT")
        return DateChunkStrategy(table_def, db2_settings, chunk_size, date_column="EFFDT")

    # Suggest best key for chunking
    suggested_key = suggest_chunk_key(key_fields)

    if suggested_key:
        # Check if it's a numeric field (good for range chunking)
        field_def = table_def.get_field(suggested_key)
        if field_def and field_def.field_type in (2, 3):  # NUMBER or SIGNED_NUMBER
            log.info(
                "using_numeric_range_strategy",
                table=table_def.sql_table_name,
                column=suggested_key,
            )
            return NumericRangeStrategy(table_def, db2_settings, chunk_size, key_column=suggested_key)

        # Check if it's a date field
        if field_def and field_def.field_type == 4:  # DATE
            log.info(
                "using_date_chunk_strategy",
                table=table_def.sql_table_name,
                column=suggested_key,
            )
            return DateChunkStrategy(table_def, db2_settings, chunk_size, date_column=suggested_key)

    # Fallback to offset/limit
    log.info("using_offset_limit_strategy", table=table_def.sql_table_name)
    return OffsetLimitStrategy(table_def, db2_settings, chunk_size)
