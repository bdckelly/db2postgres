"""
Migration orchestrator - coordinates worker processes and monitors progress.

Responsibilities:
- Build work queue from PSRECDEFN
- Spawn and manage worker pool
- Monitor system resources
- Dynamically adjust parallelism
- Track progress and checkpoints
- Handle graceful shutdown
"""

import multiprocessing
import signal
import time
from typing import Any

import structlog
from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from config.settings import Settings
from src.resource_monitor import ResourceMonitor
from src.schema.extractor import SchemaExtractor
from src.utils.progress import CheckpointManager, CheckpointStatus
from src.worker import ExtractionWorker, WorkerResult, create_extraction_task, create_stop_task

log = structlog.get_logger()
console = Console()


class MigrationOrchestrator:
    """
    Orchestrate parallel migration of PeopleSoft tables.

    Manages worker pool, distributes work, monitors resources, and tracks progress.
    """

    def __init__(
        self,
        settings: Settings,
        table_names: list[str] | None = None,
        resume: bool = False,
    ):
        """
        Initialize migration orchestrator.

        Args:
            settings: Application settings
            table_names: Optional list of specific tables to migrate
            resume: If True, resume from checkpoints
        """
        self.settings = settings
        self.table_names = table_names
        self.resume = resume

        # Work queues
        self.task_queue: multiprocessing.Queue = multiprocessing.Queue()
        self.result_queue: multiprocessing.Queue = multiprocessing.Queue()

        # Worker pool
        self.workers: list[ExtractionWorker] = []
        self.next_worker_id = 0

        # Resource monitor
        self.resource_monitor = ResourceMonitor(
            min_workers=settings.migration.min_workers,
            max_workers=settings.migration.max_workers,
        )

        # Checkpoint manager
        self.checkpoint_manager = CheckpointManager(settings.migration.checkpoint_dir)

        # Progress tracking
        self.total_tables = 0
        self.completed_tables = 0
        self.failed_tables = 0
        self.total_rows = 0

        # Shutdown flag
        self.shutdown_requested = False

        log.info(
            "orchestrator_initialized",
            min_workers=settings.migration.min_workers,
            max_workers=settings.migration.max_workers,
            resume=resume,
        )

    def build_work_queue(self) -> None:
        """
        Build work queue from PSRECDEFN.

        Prioritizes tables by size for optimal throughput.
        """
        log.info("building_work_queue")

        extractor = SchemaExtractor(self.settings.db2)

        # Get table list
        if self.table_names:
            tables_to_migrate = self.table_names
            log.info("specific_tables_requested", count=len(tables_to_migrate))
        else:
            tables_to_migrate = extractor.extract_table_list()
            log.info("all_tables_selected", count=len(tables_to_migrate))

        # Filter out completed tables if resuming
        if self.resume:
            completed = self.checkpoint_manager.get_completed_tables()
            tables_to_migrate = [t for t in tables_to_migrate if t not in completed]
            log.info("resume_filtered_completed", remaining=len(tables_to_migrate))

        self.total_tables = len(tables_to_migrate)

        # Extract table definitions (needed for chunking decisions)
        for i, table_name in enumerate(tables_to_migrate, 1):
            log.debug(
                "extracting_table_def",
                table=table_name,
                progress=f"{i}/{len(tables_to_migrate)}",
            )

            try:
                table_def = extractor.extract_table_definition(table_name)

                # Create extraction task
                task = create_extraction_task(table_name, table_def)
                self.task_queue.put(task)

            except Exception as e:
                log.error("table_def_extraction_failed", table=table_name, error=str(e))
                # Continue with next table

        log.info("work_queue_built", total_tables=self.total_tables)

    def spawn_workers(self, count: int) -> None:
        """
        Spawn worker processes.

        Args:
            count: Number of workers to spawn
        """
        for _ in range(count):
            worker = ExtractionWorker(
                worker_id=self.next_worker_id,
                settings=self.settings,
                task_queue=self.task_queue,
                result_queue=self.result_queue,
            )
            worker.start()
            self.workers.append(worker)
            self.next_worker_id += 1

        log.info("workers_spawned", count=count, total_workers=len(self.workers))

    def terminate_workers(self, count: int) -> None:
        """
        Terminate worker processes.

        Args:
            count: Number of workers to terminate
        """
        for _ in range(count):
            if self.workers:
                worker = self.workers.pop()
                # Send stop signal
                self.task_queue.put(create_stop_task())
                # Wait for worker to finish
                worker.join(timeout=5.0)
                if worker.is_alive():
                    worker.terminate()
                    worker.join()

        log.info("workers_terminated", count=count, remaining_workers=len(self.workers))

    def adjust_worker_pool(self) -> None:
        """Adjust worker pool size based on resource monitor recommendation."""
        if not self.resource_monitor.should_adjust_workers():
            return

        current_count = len(self.workers)
        recommended_count = self.resource_monitor.recommended_workers()

        if recommended_count > current_count:
            # Spawn more workers
            to_spawn = recommended_count - current_count
            self.spawn_workers(to_spawn)
            self.resource_monitor.update_worker_count(recommended_count)

        elif recommended_count < current_count:
            # Terminate workers
            to_terminate = current_count - recommended_count
            self.terminate_workers(to_terminate)
            self.resource_monitor.update_worker_count(recommended_count)

    def process_results(self) -> None:
        """Process results from result queue (non-blocking)."""
        while not self.result_queue.empty():
            try:
                result: WorkerResult = self.result_queue.get_nowait()

                if result.success:
                    self.completed_tables += 1
                    self.total_rows += result.rows_extracted
                    log.info(
                        "table_completed",
                        table=result.table_name,
                        rows=result.rows_extracted,
                        duration=result.duration_seconds,
                        worker=result.worker_id,
                    )
                else:
                    self.failed_tables += 1
                    log.error(
                        "table_failed",
                        table=result.table_name,
                        error=result.error,
                        worker=result.worker_id,
                    )

            except Exception:
                break

    def create_progress_display(self) -> Table:
        """
        Create Rich table for progress display.

        Returns:
            Table: Formatted progress table
        """
        # Collect metrics
        metrics = self.resource_monitor.collect_metrics()

        # Calculate progress
        progress_pct = (
            (self.completed_tables / self.total_tables * 100) if self.total_tables > 0 else 0
        )

        # Create table
        table = Table(title="PS82 to PostgreSQL Migration", show_header=False, box=None)
        table.add_column("Key", style="cyan", width=20)
        table.add_column("Value", style="green")

        # Add rows
        table.add_row(
            "Progress",
            f"{self.completed_tables}/{self.total_tables} tables ({progress_pct:.1f}%)",
        )
        table.add_row("Workers", f"{len(self.workers)} active")
        table.add_row("CPU", f"{metrics.cpu_percent:.1f}%")
        table.add_row("Memory", f"{metrics.memory_percent:.1f}%")
        table.add_row("Total Rows", f"{self.total_rows:,}")

        if self.failed_tables > 0:
            table.add_row("Failed", f"{self.failed_tables}", style="red")

        return table

    def run(self) -> bool:
        """
        Run migration orchestration.

        Returns:
            bool: True if migration completed successfully, False otherwise
        """
        log.info("migration_started")

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            # Build work queue
            console.print("\n[bold cyan]Building work queue...[/bold cyan]")
            self.build_work_queue()

            if self.total_tables == 0:
                console.print("[yellow]No tables to migrate.[/yellow]\n")
                return True

            # Spawn initial workers
            initial_workers = self.settings.migration.min_workers
            self.spawn_workers(initial_workers)
            self.resource_monitor.update_worker_count(initial_workers)

            console.print(f"\n[bold cyan]Starting migration with {initial_workers} workers...[/bold cyan]\n")

            # Main orchestration loop with live display
            with Live(self.create_progress_display(), refresh_per_second=1, console=console) as live:
                while not self.shutdown_requested:
                    # Process results
                    self.process_results()

                    # Check if migration complete
                    if self.completed_tables + self.failed_tables >= self.total_tables:
                        log.info("migration_complete")
                        break

                    # Adjust worker pool based on resources
                    self.adjust_worker_pool()

                    # Update display
                    live.update(self.create_progress_display())

                    # Sleep briefly
                    time.sleep(1)

            # Shutdown workers
            console.print("\n[bold cyan]Shutting down workers...[/bold cyan]")
            self.shutdown_workers()

            # Print summary
            self._print_summary()

            success = self.failed_tables == 0
            log.info(
                "migration_finished",
                completed=self.completed_tables,
                failed=self.failed_tables,
                success=success,
            )

            return success

        except Exception as e:
            log.error("migration_failed", error=str(e))
            console.print(f"\n[bold red]Migration failed: {e}[/bold red]\n")
            self.shutdown_workers()
            return False

    def shutdown_workers(self) -> None:
        """Gracefully shutdown all workers."""
        log.info("shutting_down_workers", count=len(self.workers))

        # Send stop signals
        for _ in self.workers:
            self.task_queue.put(create_stop_task())

        # Wait for workers to finish
        for worker in self.workers:
            worker.join(timeout=10.0)
            if worker.is_alive():
                log.warning("worker_not_responding_terminating", worker_id=worker.worker_id)
                worker.terminate()
                worker.join()

        self.workers.clear()
        log.info("all_workers_shutdown")

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals."""
        log.warning("shutdown_signal_received", signal=signum)
        console.print("\n[yellow]Shutdown requested. Finishing current tasks...[/yellow]\n")
        self.shutdown_requested = True

    def _print_summary(self) -> None:
        """Print migration summary."""
        console.print("\n" + "=" * 80)
        console.print("[bold cyan]MIGRATION SUMMARY[/bold cyan]")
        console.print("=" * 80)
        console.print(f"Total Tables: {self.total_tables}")
        console.print(f"[green]Completed: {self.completed_tables}[/green]")

        if self.failed_tables > 0:
            console.print(f"[red]Failed: {self.failed_tables}[/red]")

        console.print(f"Total Rows Extracted: {self.total_rows:,}")

        # Resource summary
        console.print("\n" + self.resource_monitor.format_performance_summary())
        console.print("=" * 80 + "\n")

        # List failed tables if any
        if self.failed_tables > 0:
            failed_checkpoints = self.checkpoint_manager.get_failed_tables()
            if failed_checkpoints:
                console.print("\n[bold red]Failed Tables:[/bold red]")
                for checkpoint in failed_checkpoints:
                    console.print(f"  - {checkpoint.table}: {checkpoint.error}")
                console.print()
