"""
Worker process for parallel table extraction and loading.

Supports two modes:
1. STREAMING MODE (default, use_streaming=True):
   - Streams data directly from DB2 to PostgreSQL
   - No intermediate files - near-zero memory footprint
   - Uses psycopg3's COPY protocol with write_row()
   - Best performance for most use cases

2. FILE-BASED MODE (use_streaming=False):
   - Extracts data to CSV staging files
   - Then loads from CSV using PostgreSQL COPY
   - Useful when you need to inspect/validate data before loading

Each worker:
- Receives table extraction tasks from queue
- Extracts data from DB2
- Loads data into PostgreSQL (streaming or via files)
- Updates checkpoints for restart capability
- Reports results back to orchestrator
"""

import multiprocessing
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import structlog

from config.settings import Settings
from src.extraction import (
    ExtractionCursor,
    PostgresCopySerializer,
    determine_chunking_strategy,
    get_staging_file_path,
)
from src.loading.bulk_loader import BulkLoader, BulkLoadError, StreamingLoader
from src.schema.extractor import SchemaExtractor, TableDefinition
from src.utils.progress import Checkpoint, CheckpointManager, CheckpointStatus

log = structlog.get_logger()


class TaskType(str, Enum):
    """Type of worker task."""

    EXTRACT_TABLE = "extract_table"
    STOP = "stop"


@dataclass
class WorkerTask:
    """Task for worker to process."""

    task_type: TaskType
    table_name: str | None = None
    table_def: TableDefinition | None = None
    chunk_id: int | None = None
    resume: bool = False  # If True, resume from checkpoint; if False, start fresh


@dataclass
class WorkerResult:
    """Result from worker task."""

    worker_id: int
    table_name: str
    success: bool
    rows_extracted: int = 0
    rows_loaded: int = 0
    chunks_processed: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class ExtractionWorker(multiprocessing.Process):
    """
    Worker process for extracting tables from DB2.

    Runs in separate process with own DB2 connection.
    """

    def __init__(
        self,
        worker_id: int,
        settings: Settings,
        task_queue: multiprocessing.Queue,
        result_queue: multiprocessing.Queue,
    ):
        """
        Initialize extraction worker.

        Args:
            worker_id: Unique worker identifier
            settings: Application settings
            task_queue: Queue to receive tasks from
            result_queue: Queue to send results to
        """
        super().__init__(name=f"Worker-{worker_id}")
        self.worker_id = worker_id
        self.settings = settings
        self.task_queue = task_queue
        self.result_queue = result_queue

        log.info("worker_initialized", worker_id=worker_id)

    def run(self) -> None:
        """Main worker loop."""
        # Initialize logging for this process
        from src.utils.logging_config import setup_logging

        setup_logging(
            log_level=self.settings.migration.log_level,
            log_format=self.settings.migration.log_format,
            log_file=self.settings.migration.log_dir / f"worker_{self.worker_id}.log",
        )

        # Bind worker context to all logs
        from src.utils.logging_config import bind_context

        bind_context(worker_id=self.worker_id)

        log.info("worker_started", worker_id=self.worker_id)

        # Initialize checkpoint manager
        checkpoint_manager = CheckpointManager(self.settings.migration.checkpoint_dir)

        try:
            while True:
                # Get task from queue (blocking)
                task: WorkerTask = self.task_queue.get()

                if task.task_type == TaskType.STOP:
                    log.info("worker_received_stop_signal", worker_id=self.worker_id)
                    break

                # Process extraction task
                result = self._process_extraction_task(task, checkpoint_manager)

                # Send result back
                self.result_queue.put(result)

        except Exception as e:
            log.error("worker_fatal_error", worker_id=self.worker_id, error=str(e))
            raise

        finally:
            log.info("worker_stopped", worker_id=self.worker_id)

    def _process_extraction_task(
        self, task: WorkerTask, checkpoint_manager: CheckpointManager
    ) -> WorkerResult:
        """
        Process table extraction task.

        Args:
            task: Extraction task
            checkpoint_manager: Checkpoint manager

        Returns:
            WorkerResult: Result of extraction
        """
        table_name = task.table_name
        table_def = task.table_def

        log.info("task_started", table=table_name)

        start_time = time.time()

        try:
            # Load or create checkpoint
            checkpoint = checkpoint_manager.load_checkpoint(table_name)

            if not checkpoint:
                # New table - create fresh checkpoint
                checkpoint = Checkpoint(
                    table=table_name,
                    status=CheckpointStatus.IN_PROGRESS,
                    started_at=None,
                )
            elif not task.resume:
                # Not resuming - reset checkpoint for fresh extraction
                log.info("resetting_checkpoint_for_fresh_start", table=table_name)
                checkpoint.rows_extracted = 0
                checkpoint.rows_loaded = 0
                checkpoint.chunk_id = None
                checkpoint.last_key = None
                checkpoint.error = None
                checkpoint.completed_at = None

            checkpoint.status = CheckpointStatus.IN_PROGRESS
            checkpoint.started_at = checkpoint.started_at or datetime.now()
            checkpoint_manager.save_checkpoint(checkpoint)

            # If table definition not provided, extract it
            if not table_def:
                extractor = SchemaExtractor(self.settings.db2)
                table_def = extractor.extract_table_definition(table_name)

            # Choose between streaming and file-based mode
            if self.settings.migration.use_streaming:
                # STREAMING MODE: Extract from DB2 and load directly to PostgreSQL
                log.info("using_streaming_mode", table=table_name)
                total_rows, rows_loaded = self._stream_table(table_def, checkpoint_manager, checkpoint)
                chunks_processed = 1  # Streaming treats entire table as one "chunk"
            else:
                # FILE-BASED MODE: Extract to CSV files, then load
                log.info("using_file_mode", table=table_name)

                # Determine chunking strategy
                strategy = determine_chunking_strategy(
                    table_def, self.settings.db2, self.settings.migration.chunk_size
                )

                chunks = strategy.generate_chunks()

                log.info(
                    "chunking_strategy_determined",
                    table=table_name,
                    chunks=len(chunks),
                    strategy=strategy.__class__.__name__,
                )

                total_rows = 0
                chunks_processed = 0

                # Extract each chunk to CSV files
                for chunk in chunks:
                    # Check if chunk already processed (for resume)
                    if checkpoint.chunk_id is not None and chunk.chunk_id <= checkpoint.chunk_id:
                        log.debug("chunk_already_processed", table=table_name, chunk_id=chunk.chunk_id)
                        continue

                    # Extract chunk
                    rows = self._extract_chunk(table_def, chunk)
                    total_rows += rows
                    chunks_processed += 1

                    # Update checkpoint
                    checkpoint.rows_extracted = total_rows
                    checkpoint.chunk_id = chunk.chunk_id
                    checkpoint_manager.save_checkpoint(checkpoint)

                    log.info(
                        "chunk_completed",
                        table=table_name,
                        chunk_id=chunk.chunk_id,
                        chunk_rows=rows,
                        total_rows=total_rows,
                    )

                # Load data into PostgreSQL from CSV files
                log.info("loading_phase_started", table=table_def.sql_table_name, rows_extracted=total_rows)
                rows_loaded = self._load_table(table_def.sql_table_name, table_def)

            # Update checkpoint with loaded rows
            checkpoint.rows_loaded = rows_loaded
            checkpoint.status = CheckpointStatus.COMPLETED
            checkpoint.completed_at = datetime.now()
            checkpoint_manager.save_checkpoint(checkpoint)

            duration = time.time() - start_time

            log.info(
                "task_completed",
                table=table_name,
                rows_extracted=total_rows,
                rows_loaded=rows_loaded,
                chunks=chunks_processed,
                duration=duration,
            )

            return WorkerResult(
                worker_id=self.worker_id,
                table_name=table_name,
                success=True,
                rows_extracted=total_rows,
                rows_loaded=rows_loaded,
                chunks_processed=chunks_processed,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time

            log.error("task_failed", table=table_name, error=str(e), duration=duration)

            # Mark as failed in checkpoint
            checkpoint = checkpoint_manager.load_checkpoint(table_name)
            if checkpoint:
                checkpoint.status = CheckpointStatus.FAILED
                checkpoint.error = str(e)
                checkpoint_manager.save_checkpoint(checkpoint)

            return WorkerResult(
                worker_id=self.worker_id,
                table_name=table_name,
                success=False,
                error=str(e),
                duration_seconds=duration,
            )

    def _extract_chunk(self, table_def: TableDefinition, chunk) -> int:
        """
        Extract single chunk from DB2 to CSV.

        Args:
            table_def: Table definition
            chunk: Chunk definition

        Returns:
            int: Number of rows extracted
        """
        table_name = table_def.sql_table_name

        log.debug("extracting_chunk", table=table_name, chunk_id=chunk.chunk_id)

        # Get staging file path
        csv_file = get_staging_file_path(
            self.settings.migration.staging_dir,
            table_name,
            chunk.chunk_id if chunk.chunk_id > 0 else None,
        )

        # Initialize serializer
        serializer = PostgresCopySerializer(csv_file)

        # Initialize cursor
        cursor = ExtractionCursor(self.settings.db2, table_name, batch_size=10_000)

        # Extract data
        row_generator = cursor.extract_chunk(
            chunk_id=chunk.chunk_id,
            where_clause=chunk.where_clause,
            params=chunk.params,
        )

        # Stream to CSV
        rows_written = serializer.write_streaming(row_generator)

        log.debug(
            "chunk_extracted",
            table=table_name,
            chunk_id=chunk.chunk_id,
            rows=rows_written,
            csv_file=str(csv_file),
        )

        return rows_written

    def _load_table(self, table_name: str, table_def: TableDefinition) -> int:
        """
        Load extracted CSV files into PostgreSQL.

        Args:
            table_name: Table name (will be lowercased for PostgreSQL)
            table_def: Source table definition for auto-creating table if needed

        Returns:
            int: Number of rows loaded
        """
        # Check if loading is disabled
        if self.settings.migration.skip_loading:
            log.info("loading_skipped", table=table_name, reason="skip_loading enabled")
            return 0

        # PostgreSQL table name is lowercase
        pg_table_name = table_name.lower()
        staging_dir = self.settings.migration.staging_dir

        log.info("loading_table", table=pg_table_name, staging_dir=str(staging_dir))

        # Find all CSV files for this table
        table_staging_dir = staging_dir / table_name
        csv_files = sorted(table_staging_dir.glob("*.csv"))

        if not csv_files:
            log.warning("no_csv_files_found", table=table_name, staging_dir=str(table_staging_dir))
            return 0

        log.info("found_csv_files", table=table_name, count=len(csv_files))

        # Determine if we should drop indexes based on threshold
        drop_indexes = len(csv_files) > self.settings.migration.drop_indexes_threshold

        loader = BulkLoader(
            postgres_settings=self.settings.postgres,
            table_name=pg_table_name,
            drop_indexes=drop_indexes,
            ssh_settings=self.settings.ssh_tunnel,
        )

        try:
            # Truncate table if configured (for re-runs)
            # Only truncate if table already exists
            if self.settings.migration.truncate_before_load and loader.table_exists():
                loader.truncate_table()

            # Load with index management (handles drop/rebuild and auto-create)
            rows_loaded = loader.load_with_index_management(csv_files, table_def=table_def)

            log.info(
                "table_loaded",
                table=pg_table_name,
                rows_loaded=rows_loaded,
                csv_files=len(csv_files),
                drop_indexes=drop_indexes,
            )

            return rows_loaded

        except BulkLoadError as e:
            log.error("bulk_load_failed", table=pg_table_name, error=str(e))
            raise

    def _stream_table(
        self,
        table_def: TableDefinition,
        checkpoint_manager: CheckpointManager,
        checkpoint: Checkpoint,
    ) -> tuple[int, int]:
        """
        Stream data directly from DB2 to PostgreSQL without intermediate files.

        This is the "gold standard" for ETL - rows flow from source to target
        with minimal memory usage and maximum throughput.

        Args:
            table_def: Table definition
            checkpoint_manager: Checkpoint manager
            checkpoint: Current checkpoint

        Returns:
            tuple[int, int]: (rows_extracted, rows_loaded)
        """
        table_name = table_def.sql_table_name
        pg_table_name = table_name.lower()

        log.info("stream_table_started", table=table_name)

        # Initialize streaming loader
        loader = StreamingLoader(
            postgres_settings=self.settings.postgres,
            table_name=pg_table_name,
            ssh_settings=self.settings.ssh_tunnel,
        )

        # Initialize extraction cursor
        cursor = ExtractionCursor(self.settings.db2, table_name, batch_size=10_000)

        # Create generator that extracts from DB2
        def db2_row_generator():
            """Generator that yields rows from DB2."""
            row_gen = cursor.extract_chunk(
                chunk_id=0,
                where_clause="1=1",  # Extract all rows
                params=(),
            )
            for row in row_gen:
                yield row

        # Stream directly to PostgreSQL
        rows_loaded = loader.stream_load(
            row_generator=db2_row_generator(),
            table_def=table_def,
            truncate_first=self.settings.migration.truncate_before_load,
        )

        # rows_extracted equals rows_loaded in streaming mode
        rows_extracted = rows_loaded

        # Update checkpoint
        checkpoint.rows_extracted = rows_extracted
        checkpoint.rows_loaded = rows_loaded
        checkpoint.chunk_id = 0
        checkpoint_manager.save_checkpoint(checkpoint)

        log.info(
            "stream_table_completed",
            table=table_name,
            rows_extracted=rows_extracted,
            rows_loaded=rows_loaded,
        )

        return rows_extracted, rows_loaded


def create_stop_task() -> WorkerTask:
    """
    Create stop signal task.

    Returns:
        WorkerTask: Stop task
    """
    return WorkerTask(task_type=TaskType.STOP)


def create_extraction_task(
    table_name: str,
    table_def: TableDefinition | None = None,
    resume: bool = False,
) -> WorkerTask:
    """
    Create table extraction task.

    Args:
        table_name: Table name (record name)
        table_def: Optional table definition (will be extracted if not provided)
        resume: If True, resume from checkpoint; if False, start fresh

    Returns:
        WorkerTask: Extraction task
    """
    return WorkerTask(
        task_type=TaskType.EXTRACT_TABLE,
        table_name=table_name,
        table_def=table_def,
        resume=resume,
    )
