"""
Unit tests for checkpoint and progress tracking.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.utils.progress import Checkpoint, CheckpointManager, CheckpointStatus


class TestCheckpoint:
    """Test Checkpoint dataclass."""

    def test_checkpoint_creation(self):
        """Test creating a checkpoint."""
        checkpoint = Checkpoint(
            table="PS_VOUCHER",
            status=CheckpointStatus.IN_PROGRESS,
            rows_extracted=1000,
            rows_loaded=1000,
        )
        assert checkpoint.table == "PS_VOUCHER"
        assert checkpoint.status == CheckpointStatus.IN_PROGRESS
        assert checkpoint.rows_extracted == 1000

    def test_checkpoint_to_dict(self):
        """Test converting checkpoint to dict."""
        started_at = datetime(2025, 1, 1, 12, 0, 0)
        checkpoint = Checkpoint(
            table="PS_VOUCHER",
            status=CheckpointStatus.COMPLETED,
            rows_extracted=5000,
            started_at=started_at,
        )
        data = checkpoint.to_dict()

        assert data["table"] == "PS_VOUCHER"
        assert data["status"] == "completed"
        assert data["rows_extracted"] == 5000
        assert data["started_at"] == "2025-01-01T12:00:00"

    def test_checkpoint_from_dict(self):
        """Test creating checkpoint from dict."""
        data = {
            "table": "PS_VOUCHER",
            "status": "in_progress",
            "rows_extracted": 2500,
            "rows_loaded": 2500,
            "chunk_id": 5,
            "started_at": "2025-01-01T10:00:00",
            "updated_at": "2025-01-01T10:30:00",
            "completed_at": None,
            "last_key": {"BUSINESS_UNIT": "US001"},
            "error": None,
        }
        checkpoint = Checkpoint.from_dict(data)

        assert checkpoint.table == "PS_VOUCHER"
        assert checkpoint.status == CheckpointStatus.IN_PROGRESS
        assert checkpoint.rows_extracted == 2500
        assert checkpoint.chunk_id == 5
        assert checkpoint.last_key == {"BUSINESS_UNIT": "US001"}
        assert isinstance(checkpoint.started_at, datetime)


class TestCheckpointManager:
    """Test CheckpointManager."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for checkpoints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager(self, temp_dir):
        """Create CheckpointManager instance."""
        return CheckpointManager(temp_dir)

    def test_save_and_load_checkpoint(self, manager):
        """Test saving and loading checkpoint."""
        checkpoint = Checkpoint(
            table="PS_VOUCHER",
            status=CheckpointStatus.IN_PROGRESS,
            rows_extracted=1000,
        )

        # Save
        manager.save_checkpoint(checkpoint)

        # Load
        loaded = manager.load_checkpoint("PS_VOUCHER")
        assert loaded is not None
        assert loaded.table == "PS_VOUCHER"
        assert loaded.status == CheckpointStatus.IN_PROGRESS
        assert loaded.rows_extracted == 1000

    def test_load_nonexistent_checkpoint(self, manager):
        """Test loading checkpoint that doesn't exist."""
        result = manager.load_checkpoint("NONEXISTENT_TABLE")
        assert result is None

    def test_delete_checkpoint(self, manager, temp_dir):
        """Test deleting checkpoint."""
        checkpoint = Checkpoint(table="PS_VOUCHER", status=CheckpointStatus.COMPLETED)
        manager.save_checkpoint(checkpoint)

        # Verify it exists
        assert manager.load_checkpoint("PS_VOUCHER") is not None

        # Delete
        manager.delete_checkpoint("PS_VOUCHER")

        # Verify it's gone
        assert manager.load_checkpoint("PS_VOUCHER") is None

    def test_list_checkpoints(self, manager):
        """Test listing all checkpoints."""
        # Create multiple checkpoints
        checkpoints = [
            Checkpoint(table="PS_VOUCHER", status=CheckpointStatus.COMPLETED, rows_extracted=5000),
            Checkpoint(table="PS_VCHR_LINE", status=CheckpointStatus.IN_PROGRESS, rows_extracted=2500),
            Checkpoint(table="PS_PAYMENT", status=CheckpointStatus.FAILED, error="Connection error"),
        ]

        for cp in checkpoints:
            manager.save_checkpoint(cp)

        # List all
        all_checkpoints = manager.list_checkpoints()
        assert len(all_checkpoints) == 3

        # Check each status is correct
        statuses = {cp.table: cp.status for cp in all_checkpoints}
        assert statuses["PS_VOUCHER"] == CheckpointStatus.COMPLETED
        assert statuses["PS_VCHR_LINE"] == CheckpointStatus.IN_PROGRESS
        assert statuses["PS_PAYMENT"] == CheckpointStatus.FAILED

    def test_get_completed_tables(self, manager):
        """Test getting completed tables."""
        checkpoints = [
            Checkpoint(table="PS_VOUCHER", status=CheckpointStatus.COMPLETED),
            Checkpoint(table="PS_VCHR_LINE", status=CheckpointStatus.IN_PROGRESS),
            Checkpoint(table="PS_PAYMENT", status=CheckpointStatus.COMPLETED),
        ]

        for cp in checkpoints:
            manager.save_checkpoint(cp)

        completed = manager.get_completed_tables()
        assert len(completed) == 2
        assert "PS_VOUCHER" in completed
        assert "PS_PAYMENT" in completed
        assert "PS_VCHR_LINE" not in completed

    def test_get_failed_tables(self, manager):
        """Test getting failed tables."""
        checkpoints = [
            Checkpoint(table="PS_VOUCHER", status=CheckpointStatus.COMPLETED),
            Checkpoint(table="PS_VCHR_LINE", status=CheckpointStatus.FAILED, error="Error 1"),
            Checkpoint(table="PS_PAYMENT", status=CheckpointStatus.FAILED, error="Error 2"),
        ]

        for cp in checkpoints:
            manager.save_checkpoint(cp)

        failed = manager.get_failed_tables()
        assert len(failed) == 2
        assert all(cp.status == CheckpointStatus.FAILED for cp in failed)

    def test_get_migration_stats(self, manager):
        """Test getting migration statistics."""
        checkpoints = [
            Checkpoint(table="T1", status=CheckpointStatus.COMPLETED, rows_extracted=1000, rows_loaded=1000),
            Checkpoint(table="T2", status=CheckpointStatus.COMPLETED, rows_extracted=2000, rows_loaded=2000),
            Checkpoint(table="T3", status=CheckpointStatus.IN_PROGRESS, rows_extracted=500, rows_loaded=0),
            Checkpoint(table="T4", status=CheckpointStatus.FAILED, rows_extracted=0, rows_loaded=0),
        ]

        for cp in checkpoints:
            manager.save_checkpoint(cp)

        stats = manager.get_migration_stats()

        assert stats["total_tables"] == 4
        assert stats["completed"] == 2
        assert stats["in_progress"] == 1
        assert stats["failed"] == 1
        assert stats["total_rows_extracted"] == 3500
        assert stats["total_rows_loaded"] == 3000

    def test_reset_checkpoint(self, manager):
        """Test resetting checkpoint."""
        checkpoint = Checkpoint(
            table="PS_VOUCHER",
            status=CheckpointStatus.FAILED,
            rows_extracted=1000,
            error="Some error",
        )
        manager.save_checkpoint(checkpoint)

        # Reset
        manager.reset_checkpoint("PS_VOUCHER")

        # Load and verify
        loaded = manager.load_checkpoint("PS_VOUCHER")
        assert loaded.status == CheckpointStatus.PENDING
        assert loaded.rows_extracted == 0
        assert loaded.error is None

    def test_atomic_write(self, manager, temp_dir):
        """Test checkpoint writes are atomic."""
        checkpoint = Checkpoint(table="PS_VOUCHER", status=CheckpointStatus.IN_PROGRESS)

        # Save checkpoint
        manager.save_checkpoint(checkpoint)

        # Verify no .tmp file exists (atomic rename completed)
        tmp_files = list(temp_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

        # Verify checkpoint file exists
        checkpoint_file = temp_dir / "PS_VOUCHER.json"
        assert checkpoint_file.exists()

        # Verify it's valid JSON
        with open(checkpoint_file) as f:
            data = json.load(f)
            assert data["table"] == "PS_VOUCHER"
