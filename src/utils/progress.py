"""
Progress tracking and checkpoint management for migration restarts.

Provides atomic checkpoint writes and recovery from partial migrations.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


class CheckpointStatus(str, Enum):
    """Status of table migration."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Checkpoint:
    """
    Checkpoint data for a table migration.

    Tracks progress for resumable migrations with chunked extraction.
    """

    table: str
    status: CheckpointStatus
    rows_extracted: int = 0
    rows_loaded: int = 0
    chunk_id: int | None = None
    last_key: dict[str, Any] | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert checkpoint to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert datetime objects to ISO format strings
        for key in ("started_at", "updated_at", "completed_at"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        # Convert enum to string
        data["status"] = data["status"].value if isinstance(data["status"], Enum) else data["status"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        """Create checkpoint from dictionary loaded from JSON."""
        # Convert ISO format strings back to datetime
        for key in ("started_at", "updated_at", "completed_at"):
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])

        # Convert status string to enum
        if isinstance(data.get("status"), str):
            data["status"] = CheckpointStatus(data["status"])

        return cls(**data)


class CheckpointManager:
    """
    Manage checkpoints for table migrations.

    Provides atomic writes, recovery, and progress tracking.
    """

    def __init__(self, checkpoint_dir: Path):
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_dir: Directory to store checkpoint files
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        log.info("checkpoint_manager_initialized", checkpoint_dir=str(self.checkpoint_dir))

    def _get_checkpoint_path(self, table_name: str) -> Path:
        """
        Get checkpoint file path for table.

        Args:
            table_name: Table name

        Returns:
            Path: Checkpoint file path
        """
        # Sanitize table name for filename
        safe_name = table_name.replace("/", "_").replace("\\", "_")
        return self.checkpoint_dir / f"{safe_name}.json"

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        """
        Save checkpoint atomically.

        Uses write-to-temp-then-rename pattern for atomicity.

        Args:
            checkpoint: Checkpoint to save
        """
        checkpoint_path = self._get_checkpoint_path(checkpoint.table)
        temp_path = checkpoint_path.with_suffix(".tmp")

        try:
            # Update timestamp
            checkpoint.updated_at = datetime.now()

            # Write to temporary file
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint.to_dict(), f, indent=2)

            # Atomic rename
            temp_path.replace(checkpoint_path)

            log.debug(
                "checkpoint_saved",
                table=checkpoint.table,
                status=checkpoint.status.value,
                rows_extracted=checkpoint.rows_extracted,
                rows_loaded=checkpoint.rows_loaded,
            )

        except Exception as e:
            log.error("checkpoint_save_failed", table=checkpoint.table, error=str(e))
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()
            raise

    def load_checkpoint(self, table_name: str) -> Checkpoint | None:
        """
        Load checkpoint for table.

        Args:
            table_name: Table name

        Returns:
            Checkpoint | None: Loaded checkpoint, or None if not found
        """
        checkpoint_path = self._get_checkpoint_path(table_name)

        if not checkpoint_path.exists():
            log.debug("checkpoint_not_found", table=table_name)
            return None

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            checkpoint = Checkpoint.from_dict(data)
            log.debug(
                "checkpoint_loaded",
                table=table_name,
                status=checkpoint.status.value,
                rows_extracted=checkpoint.rows_extracted,
            )
            return checkpoint

        except Exception as e:
            log.error("checkpoint_load_failed", table=table_name, error=str(e))
            return None

    def delete_checkpoint(self, table_name: str) -> None:
        """
        Delete checkpoint file for table.

        Args:
            table_name: Table name
        """
        checkpoint_path = self._get_checkpoint_path(table_name)
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            log.debug("checkpoint_deleted", table=table_name)

    def list_checkpoints(self) -> list[Checkpoint]:
        """
        List all checkpoints.

        Returns:
            list[Checkpoint]: List of all checkpoints
        """
        checkpoints = []
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            try:
                with open(checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                checkpoint = Checkpoint.from_dict(data)
                checkpoints.append(checkpoint)
            except Exception as e:
                log.warning(
                    "checkpoint_load_error", file=checkpoint_file.name, error=str(e)
                )

        return checkpoints

    def get_completed_tables(self) -> set[str]:
        """
        Get set of completed table names.

        Returns:
            set[str]: Set of table names that are completed
        """
        completed = set()
        for checkpoint in self.list_checkpoints():
            if checkpoint.status == CheckpointStatus.COMPLETED:
                completed.add(checkpoint.table)

        log.info("completed_tables_loaded", count=len(completed))
        return completed

    def get_failed_tables(self) -> list[Checkpoint]:
        """
        Get list of failed table checkpoints.

        Returns:
            list[Checkpoint]: List of checkpoints with failed status
        """
        failed = []
        for checkpoint in self.list_checkpoints():
            if checkpoint.status == CheckpointStatus.FAILED:
                failed.append(checkpoint)

        log.info("failed_tables_loaded", count=len(failed))
        return failed

    def get_in_progress_tables(self) -> list[Checkpoint]:
        """
        Get list of in-progress table checkpoints.

        Returns:
            list[Checkpoint]: List of checkpoints with in_progress status
        """
        in_progress = []
        for checkpoint in self.list_checkpoints():
            if checkpoint.status == CheckpointStatus.IN_PROGRESS:
                in_progress.append(checkpoint)

        log.info("in_progress_tables_loaded", count=len(in_progress))
        return in_progress

    def get_migration_stats(self) -> dict[str, Any]:
        """
        Get overall migration statistics.

        Returns:
            dict: Statistics including counts by status, total rows, etc.
        """
        checkpoints = self.list_checkpoints()

        stats = {
            "total_tables": len(checkpoints),
            "completed": 0,
            "in_progress": 0,
            "failed": 0,
            "pending": 0,
            "total_rows_extracted": 0,
            "total_rows_loaded": 0,
        }

        for checkpoint in checkpoints:
            stats[checkpoint.status.value] += 1
            stats["total_rows_extracted"] += checkpoint.rows_extracted
            stats["total_rows_loaded"] += checkpoint.rows_loaded

        return stats

    def reset_checkpoint(self, table_name: str) -> None:
        """
        Reset checkpoint to pending status (for retry).

        Args:
            table_name: Table name to reset
        """
        checkpoint = self.load_checkpoint(table_name)
        if checkpoint:
            checkpoint.status = CheckpointStatus.PENDING
            checkpoint.rows_extracted = 0
            checkpoint.rows_loaded = 0
            checkpoint.chunk_id = None
            checkpoint.last_key = None
            checkpoint.error = None
            checkpoint.completed_at = None
            self.save_checkpoint(checkpoint)
            log.info("checkpoint_reset", table=table_name)
