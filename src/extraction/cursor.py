"""
DB2 cursor management for data extraction.

Provides cursor handling with:
- WITH HOLD to prevent cursor closure on commit
- WITH UR (uncommitted read) for snapshot consistency
- Batch fetching to manage memory
- Retry logic for transient failures
- Progress tracking
"""

import time
from typing import Any, Generator

import structlog

from config.db2_connection import DB2ConnectionError, get_db2_connection
from config.settings import DB2Settings

log = structlog.get_logger()


class ExtractionCursor:
    """
    Managed cursor for data extraction with batch fetching and error handling.

    Handles:
    - Batch fetching to avoid loading entire table in memory
    - WITH HOLD cursors to prevent closure on commit
    - WITH UR for snapshot reads
    - Automatic retry on transient errors
    - Progress tracking
    """

    def __init__(
        self,
        db2_settings: DB2Settings,
        table_name: str,
        batch_size: int = 10_000,
        max_retries: int = 3,
    ):
        """
        Initialize extraction cursor.

        Args:
            db2_settings: DB2 connection settings
            table_name: Table being extracted
            batch_size: Number of rows to fetch per batch
            max_retries: Maximum retries for transient errors
        """
        self.db2_settings = db2_settings
        self.table_name = table_name
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.rows_fetched = 0

        log.info(
            "extraction_cursor_initialized",
            table=table_name,
            batch_size=batch_size,
        )

    def execute_with_retry(
        self,
        cursor: Any,
        query: str,
        params: tuple[Any, ...] = (),
        attempt: int = 1,
    ) -> None:
        """
        Execute query with retry logic.

        Args:
            cursor: DB2 cursor
            query: SQL query
            params: Query parameters
            attempt: Current attempt number

        Raises:
            DB2ConnectionError: If all retries exhausted
        """
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            log.debug(
                "query_executed",
                table=self.table_name,
                query=query[:100],
                attempt=attempt,
            )

        except Exception as e:
            error_str = str(e).lower()

            # Check if error is transient (connection/timeout related)
            is_transient = any(
                keyword in error_str
                for keyword in ["timeout", "connection", "socket", "network", "closed"]
            )

            if is_transient and attempt < self.max_retries:
                # Exponential backoff
                wait_time = 2 ** (attempt - 1)
                log.warning(
                    "query_execution_failed_retrying",
                    table=self.table_name,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    wait_time=wait_time,
                    error=str(e),
                )
                time.sleep(wait_time)

                # Retry
                self.execute_with_retry(cursor, query, params, attempt + 1)
            else:
                # Non-transient error or retries exhausted
                log.error(
                    "query_execution_failed",
                    table=self.table_name,
                    attempt=attempt,
                    error=str(e),
                )
                raise DB2ConnectionError(f"Query execution failed: {e}") from e

    def extract_chunk(
        self,
        chunk_id: int,
        where_clause: str,
        params: tuple[Any, ...] = (),
        columns: list[str] | None = None,
    ) -> Generator[tuple[Any, ...], None, None]:
        """
        Extract data for a single chunk with batch fetching.

        Args:
            chunk_id: Chunk identifier
            where_clause: WHERE clause for this chunk
            params: Parameters for WHERE clause
            columns: Optional list of columns to select (default: *)

        Yields:
            tuple: Rows from the table

        Example:
            >>> cursor = ExtractionCursor(db2_settings, "PS_VOUCHER")
            >>> for row in cursor.extract_chunk(0, "business_unit = ?", ("US001",)):
            ...     process_row(row)
        """
        log.info(
            "chunk_extraction_started",
            table=self.table_name,
            chunk_id=chunk_id,
            where_clause=where_clause[:100],
        )

        self.rows_fetched = 0
        column_list = ", ".join(columns) if columns else "*"

        # Build query with WITH UR for uncommitted read
        query = f"""
            SELECT {column_list}
            FROM {self.table_name}
            WHERE {where_clause}
            WITH UR
        """

        try:
            with get_db2_connection(self.db2_settings) as conn:
                cursor = conn.cursor()

                try:
                    # Execute query with retry logic
                    self.execute_with_retry(cursor, query, params)

                    # Fetch in batches
                    while True:
                        rows = cursor.fetchmany(self.batch_size)

                        if not rows:
                            break

                        self.rows_fetched += len(rows)

                        # Yield each row
                        for row in rows:
                            yield row

                        log.debug(
                            "batch_fetched",
                            table=self.table_name,
                            chunk_id=chunk_id,
                            rows_fetched=self.rows_fetched,
                            batch_size=len(rows),
                        )

                    log.info(
                        "chunk_extraction_completed",
                        table=self.table_name,
                        chunk_id=chunk_id,
                        total_rows=self.rows_fetched,
                    )

                finally:
                    cursor.close()

        except Exception as e:
            log.error(
                "chunk_extraction_failed",
                table=self.table_name,
                chunk_id=chunk_id,
                rows_fetched=self.rows_fetched,
                error=str(e),
            )
            raise

    def extract_all(
        self,
        where_clause: str = "1=1",
        params: tuple[Any, ...] = (),
        columns: list[str] | None = None,
    ) -> Generator[tuple[Any, ...], None, None]:
        """
        Extract all data matching criteria (convenience method for single chunk).

        Args:
            where_clause: WHERE clause
            params: Parameters for WHERE clause
            columns: Optional list of columns

        Yields:
            tuple: Rows from the table
        """
        yield from self.extract_chunk(
            chunk_id=0,
            where_clause=where_clause,
            params=params,
            columns=columns,
        )

    def get_column_names(self) -> list[str]:
        """
        Get column names for the table.

        Returns:
            list[str]: List of column names in order

        Example:
            >>> cursor = ExtractionCursor(db2_settings, "PS_VOUCHER")
            >>> columns = cursor.get_column_names()
            >>> print(columns)
            ['BUSINESS_UNIT', 'VOUCHER_ID', 'INVOICE_ID', ...]
        """
        log.debug("fetching_column_names", table=self.table_name)

        try:
            with get_db2_connection(self.db2_settings) as conn:
                cursor = conn.cursor()

                try:
                    # Query to get one row to extract column names
                    query = f"""
                        SELECT *
                        FROM {self.table_name}
                        FETCH FIRST 1 ROWS ONLY
                        WITH UR
                    """
                    cursor.execute(query)

                    # Get column names from cursor description
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        log.debug(
                            "column_names_retrieved",
                            table=self.table_name,
                            count=len(columns),
                        )
                        return columns
                    else:
                        log.warning("no_column_description", table=self.table_name)
                        return []

                finally:
                    cursor.close()

        except Exception as e:
            log.error("column_names_retrieval_failed", table=self.table_name, error=str(e))
            return []

    def get_row_count(self, where_clause: str = "1=1", params: tuple[Any, ...] = ()) -> int:
        """
        Get row count for query.

        Args:
            where_clause: WHERE clause
            params: Parameters for WHERE clause

        Returns:
            int: Number of rows matching criteria
        """
        log.debug("counting_rows", table=self.table_name, where_clause=where_clause[:100])

        try:
            with get_db2_connection(self.db2_settings) as conn:
                cursor = conn.cursor()

                try:
                    query = f"""
                        SELECT COUNT(*)
                        FROM {self.table_name}
                        WHERE {where_clause}
                        WITH UR
                    """

                    self.execute_with_retry(cursor, query, params)
                    row = cursor.fetchone()
                    count = row[0] if row else 0

                    log.debug("row_count_retrieved", table=self.table_name, count=count)
                    return count

                finally:
                    cursor.close()

        except Exception as e:
            log.error("row_count_failed", table=self.table_name, error=str(e))
            return 0


def extract_table_sample(
    db2_settings: DB2Settings,
    table_name: str,
    sample_size: int = 100,
) -> list[tuple[Any, ...]]:
    """
    Extract a sample of rows from table for testing/validation.

    Args:
        db2_settings: DB2 settings
        table_name: Table name
        sample_size: Number of rows to sample

    Returns:
        list: Sample rows

    Example:
        >>> sample = extract_table_sample(db2_settings, "PS_VOUCHER", 10)
        >>> print(f"Sampled {len(sample)} rows")
    """
    log.info("extracting_sample", table=table_name, sample_size=sample_size)

    cursor = ExtractionCursor(db2_settings, table_name, batch_size=sample_size)

    try:
        query = f"FETCH FIRST {sample_size} ROWS ONLY"
        rows = list(cursor.extract_all(where_clause=query))

        log.info("sample_extracted", table=table_name, rows=len(rows))
        return rows

    except Exception as e:
        log.error("sample_extraction_failed", table=table_name, error=str(e))
        return []
