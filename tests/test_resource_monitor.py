"""
Unit tests for resource monitoring and dynamic parallelism.
"""

import pytest

from src.resource_monitor import ResourceMetrics, ResourceMonitor


class TestResourceMetrics:
    """Test ResourceMetrics dataclass."""

    def test_create_metrics(self):
        """Test creating resource metrics."""
        metrics = ResourceMetrics(
            timestamp=1234567890.0,
            cpu_percent=50.0,
            memory_percent=60.0,
            available_memory_mb=4096.0,
            disk_io_read_mb=100.0,
            disk_io_write_mb=50.0,
            network_sent_mb=10.0,
            network_recv_mb=20.0,
        )

        assert metrics.cpu_percent == 50.0
        assert metrics.memory_percent == 60.0
        assert metrics.available_memory_mb == 4096.0

    def test_metrics_repr(self):
        """Test metrics string representation."""
        metrics = ResourceMetrics(
            timestamp=1234567890.0,
            cpu_percent=72.5,
            memory_percent=54.3,
            available_memory_mb=8192.0,
            disk_io_read_mb=0.0,
            disk_io_write_mb=0.0,
            network_sent_mb=0.0,
            network_recv_mb=0.0,
        )

        repr_str = repr(metrics)
        assert "72.5%" in repr_str
        assert "54.3%" in repr_str
        assert "8192" in repr_str


class TestResourceMonitor:
    """Test ResourceMonitor."""

    @pytest.fixture
    def monitor(self):
        """Create ResourceMonitor instance."""
        return ResourceMonitor(
            min_workers=2,
            max_workers=10,
            target_cpu_percent=75.0,
            max_cpu_percent=85.0,
            max_memory_percent=85.0,
        )

    def test_initialization(self, monitor):
        """Test monitor initialization."""
        assert monitor.min_workers == 2
        assert monitor.max_workers == 10
        assert monitor.current_workers == 2
        assert monitor.target_cpu_percent == 75.0

    def test_collect_metrics(self, monitor):
        """Test metrics collection."""
        metrics = monitor.collect_metrics()

        assert isinstance(metrics, ResourceMetrics)
        assert metrics.cpu_percent >= 0
        assert metrics.memory_percent >= 0
        assert metrics.available_memory_mb >= 0

        # Metrics should be added to history
        assert len(monitor.metrics_history) == 1

    def test_metrics_history_limit(self, monitor):
        """Test metrics history size limit."""
        # Collect more than max_history_size
        for _ in range(monitor.max_history_size + 10):
            monitor.collect_metrics()

        # Should not exceed max size
        assert len(monitor.metrics_history) == monitor.max_history_size

    def test_get_average_metrics(self, monitor):
        """Test averaging metrics."""
        # Collect a few metrics
        for _ in range(5):
            monitor.collect_metrics()

        avg = monitor.get_average_metrics(last_n=3)
        assert avg is not None
        assert isinstance(avg, ResourceMetrics)

    def test_get_average_metrics_insufficient_history(self, monitor):
        """Test averaging with insufficient history."""
        # Only collect 2 metrics
        monitor.collect_metrics()
        monitor.collect_metrics()

        # Request 5 - should return None
        avg = monitor.get_average_metrics(last_n=5)
        assert avg is None

    def test_recommended_workers_backoff_cpu(self, monitor):
        """Test backing off when CPU is high."""
        # Create metrics with high CPU
        high_cpu_metrics = ResourceMetrics(
            timestamp=0.0,
            cpu_percent=90.0,  # Above max_cpu_percent
            memory_percent=50.0,
            available_memory_mb=4096.0,
            disk_io_read_mb=0.0,
            disk_io_write_mb=0.0,
            network_sent_mb=0.0,
            network_recv_mb=0.0,
        )

        monitor.current_workers = 8
        recommended = monitor.recommended_workers(high_cpu_metrics)

        # Should recommend fewer workers
        assert recommended < monitor.current_workers
        assert recommended >= monitor.min_workers

    def test_recommended_workers_backoff_memory(self, monitor):
        """Test backing off when memory is high."""
        high_memory_metrics = ResourceMetrics(
            timestamp=0.0,
            cpu_percent=50.0,
            memory_percent=90.0,  # Above max_memory_percent
            available_memory_mb=512.0,
            disk_io_read_mb=0.0,
            disk_io_write_mb=0.0,
            network_sent_mb=0.0,
            network_recv_mb=0.0,
        )

        monitor.current_workers = 8
        recommended = monitor.recommended_workers(high_memory_metrics)

        # Should recommend fewer workers
        assert recommended < monitor.current_workers

    def test_recommended_workers_increase(self, monitor):
        """Test increasing workers when resources available."""
        low_usage_metrics = ResourceMetrics(
            timestamp=0.0,
            cpu_percent=50.0,  # Below target
            memory_percent=40.0,  # Below target
            available_memory_mb=8192.0,
            disk_io_read_mb=0.0,
            disk_io_write_mb=0.0,
            network_sent_mb=0.0,
            network_recv_mb=0.0,
        )

        monitor.current_workers = 5
        recommended = monitor.recommended_workers(low_usage_metrics)

        # Should recommend more workers
        assert recommended > monitor.current_workers
        assert recommended <= monitor.max_workers

    def test_recommended_workers_maintain(self, monitor):
        """Test maintaining current workers in optimal range."""
        optimal_metrics = ResourceMetrics(
            timestamp=0.0,
            cpu_percent=76.0,  # Near target
            memory_percent=65.0,
            available_memory_mb=4096.0,
            disk_io_read_mb=0.0,
            disk_io_write_mb=0.0,
            network_sent_mb=0.0,
            network_recv_mb=0.0,
        )

        monitor.current_workers = 6
        recommended = monitor.recommended_workers(optimal_metrics)

        # Should maintain current level
        assert recommended == monitor.current_workers

    def test_update_worker_count(self, monitor):
        """Test updating worker count."""
        assert monitor.current_workers == 2

        monitor.update_worker_count(5)

        assert monitor.current_workers == 5

    def test_should_adjust_workers_timing(self, monitor):
        """Test adjustment interval timing."""
        # Initially should be True
        assert monitor.should_adjust_workers() is True

        # Update worker count (resets timer)
        monitor.update_worker_count(monitor.current_workers)

        # Immediately after, should be False
        assert monitor.should_adjust_workers() is False

    def test_record_db2_latency(self, monitor):
        """Test recording DB2 latency."""
        monitor.collect_metrics()
        monitor.record_db2_latency(45.5)

        # Should be recorded in latest metrics
        assert monitor.metrics_history[-1].db2_avg_latency_ms == 45.5

    def test_record_postgres_throughput(self, monitor):
        """Test recording PostgreSQL throughput."""
        monitor.collect_metrics()
        monitor.record_postgres_throughput(125000.0)

        # Should be recorded in latest metrics
        assert monitor.metrics_history[-1].postgres_throughput_rows_per_sec == 125000.0

    def test_get_performance_summary(self, monitor):
        """Test getting performance summary."""
        # Collect some metrics
        for _ in range(5):
            monitor.collect_metrics()

        summary = monitor.get_performance_summary()

        assert "avg_cpu_percent" in summary
        assert "avg_memory_percent" in summary
        assert "current_workers" in summary
        assert summary["current_workers"] == monitor.current_workers

    def test_format_performance_summary(self, monitor):
        """Test formatting performance summary."""
        # Collect metrics
        monitor.collect_metrics()

        formatted = monitor.format_performance_summary()

        assert isinstance(formatted, str)
        assert "Performance Summary" in formatted
        assert "CPU" in formatted
        assert "Memory" in formatted
        assert "Workers" in formatted
