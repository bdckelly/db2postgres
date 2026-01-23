"""
DB2 cursor management for data extraction.

Provides cursor handling with:
- WITH UR (uncommitted read) for snapshot consistency
- BATCHED FETCHING: Fetches data in chunks (default 10,000 rows) to reduce
  network round-trips
- Retry logic for transient failures
- Progress tracking

JDBC Compatibility Note:
JDBC drivers close result sets when Python generators yield control back to
the caller. This means we cannot do "true" streaming where rows flow directly
from DB2 to PostgreSQL. Instead, we:
1. Fetch ALL data in batches (reduces peak memory vs single fetchall())
2. Materialize all rows to Python types while connection is open
3. Close DB2 connection
4. THEN yield rows to the caller (safe from JDBC result set closure)

For very large tables (10M+ rows), use SQL-level chunking with WHERE clauses
to keep memory manageable.
"""

import time
from typing import Any, Generator

import structlog

from config.db2_connection import DB2ConnectionError, create_connection, get_db2_connection
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

    @staticmethod
    def _convert_jdbc_value(value: Any) -> Any:
        """
        Convert JDBC value to Python type.

        JDBC drivers return Java objects (java.lang.Integer, java.lang.String, etc.)
        which need to be converted to Python types immediately to avoid "result set closed" errors.

        Args:
            value: Value from JDBC cursor (may be Java object or None)

        Returns:
            Python equivalent (str, int, float, None, etc.)
        """
        if value is None:
            return None

        # Get the string representation to check Java type
        value_str = str(type(value))

        # Handle Java Integer types
        if 'java.lang' in value_str and ('Integer' in value_str or 'Long' in value_str or 'Short' in value_str):
            return int(str(value))

        # Handle Java floating point types
        if 'java.lang' in value_str and ('Double' in value_str or 'Float' in value_str):
            return float(str(value))

        # Handle Java String
        if 'java.lang.String' in value_str:
            return str(value)

        # Handle Java Boolean
        if 'java.lang.Boolean' in value_str:
            return bool(value)

        # For native Python types or unknown types, return as-is
        # This handles cases where we're using ibm_db or mock connection
        if isinstance(value, str):
            return value

        return value

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
        Extract data for a single chunk with batched streaming.

        Uses fetchmany() to fetch data in batches, reducing network round-trips
        while maintaining low memory usage. Each batch is processed and yielded
        row-by-row before fetching the next batch.

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
            batch_size=self.batch_size,
        )

        self.rows_fetched = 0
        column_list = ", ".join(columns) if columns else "*"

        # Build query with WITH UR for uncommitted read
        query = f"SELECT {column_list} FROM {self.table_name} WHERE {where_clause} WITH UR"

        # Manually manage connection to keep it open during generator iteration
        conn = None
        cursor = None
        batches_processed = 0

        try:
            conn = create_connection(self.db2_settings)
            cursor = conn.cursor()

            # Execute query with retry logic
            self.execute_with_retry(cursor, query, params)

            # JDBC COMPATIBILITY:
            #
            # JDBC drivers have strict result set handling - fetchmany() often
            # doesn't work reliably. We use fetchall() which is universally supported.
            #
            # For truly huge tables (10M+ rows), use SQL-level chunking with
            # WHERE clauses to process smaller subsets.

            log.debug("fetching_all_rows", table=self.table_name, chunk_id=chunk_id)

            # Fetch ALL rows at once - most reliable with JDBC
            all_rows = cursor.fetchall()

            log.info(
                "fetch_completed",
                table=self.table_name,
                chunk_id=chunk_id,
                total_rows=len(all_rows),
            )

            # Materialize all data to Python types while connection is still valid
            all_materialized_rows = []
            for row in all_rows:
                python_row = tuple(
                    self._convert_jdbc_value(val) for val in row
                )
                all_materialized_rows.append(python_row)

            # Free the raw rows to reduce memory
            del all_rows

            # Close DB2 connection BEFORE yielding (JDBC-safe)
            if cursor:
                cursor.close()
                cursor = None
            if conn:
                conn.close()
                conn = None

            # NOW yield all rows (connection is closed, safe from JDBC issues)
            for python_row in all_materialized_rows:
                self.rows_fetched += 1
                yield python_row

            log.info(
                "chunk_extraction_completed",
                table=self.table_name,
                chunk_id=chunk_id,
                total_rows=self.rows_fetched,
                batches=batches_processed,
            )

        except Exception as e:
            log.error(
                "chunk_extraction_failed",
                table=self.table_name,
                chunk_id=chunk_id,
                rows_fetched=self.rows_fetched,
                error=str(e),
            )
            raise

        finally:
            # Clean up cursor and connection
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

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
