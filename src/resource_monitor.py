"""
Resource monitoring and dynamic parallelism control.

Monitors system resources and database performance to dynamically adjust worker count:
- CPU utilization (target 70-80%)
- Memory pressure (back off at 85%)
- DB2 query latency (detect mainframe saturation)
- PostgreSQL throughput (detect target bottleneck)
"""

import time
from dataclasses import dataclass
from typing import Any

import psutil
import structlog

log = structlog.get_logger()


@dataclass
class ResourceMetrics:
    """System resource metrics snapshot."""

    timestamp: float
    cpu_percent: float
    memory_percent: float
    available_memory_mb: float
    disk_io_read_mb: float
    disk_io_write_mb: float
    network_sent_mb: float
    network_recv_mb: float

    # Database-specific metrics
    db2_avg_latency_ms: float | None = None
    postgres_throughput_rows_per_sec: float | None = None

    def __repr__(self) -> str:
        return (
            f"ResourceMetrics(cpu={self.cpu_percent:.1f}%, "
            f"mem={self.memory_percent:.1f}%, "
            f"avail_mem={self.available_memory_mb:.0f}MB)"
        )


class ResourceMonitor:
    """
    Monitor system resources and recommend worker count adjustments.

    Uses control algorithm to balance resource utilization with throughput.
    """

    def __init__(
        self,
        min_workers: int = 2,
        max_workers: int = 20,
        target_cpu_percent: float = 75.0,
        max_cpu_percent: float = 85.0,
        max_memory_percent: float = 85.0,
        adjustment_interval: int = 30,
    ):
        """
        Initialize resource monitor.

        Args:
            min_workers: Minimum worker count
            max_workers: Maximum worker count
            target_cpu_percent: Target CPU utilization
            max_cpu_percent: Maximum CPU before backing off
            max_memory_percent: Maximum memory before backing off
            adjustment_interval: Seconds between adjustments
        """
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.target_cpu_percent = target_cpu_percent
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_percent = max_memory_percent
        self.adjustment_interval = adjustment_interval

        self.current_workers = min_workers
        self.last_adjustment_time = time.time()
        self.metrics_history: list[ResourceMetrics] = []
        self.max_history_size = 60  # Keep last 60 measurements

        # Baseline metrics (captured at start)
        self._baseline_metrics = self._capture_baseline()

        log.info(
            "resource_monitor_initialized",
            min_workers=min_workers,
            max_workers=max_workers,
            target_cpu=target_cpu_percent,
            max_cpu=max_cpu_percent,
            max_memory=max_memory_percent,
        )

    def _capture_baseline(self) -> ResourceMetrics:
        """Capture baseline system metrics before migration starts."""
        cpu_percent = psutil.cpu_percent(interval=1.0)
        memory = psutil.virtual_memory()
        disk_io = psutil.disk_io_counters()
        net_io = psutil.net_io_counters()

        baseline = ResourceMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            available_memory_mb=memory.available / (1024 * 1024),
            disk_io_read_mb=(disk_io.read_bytes / (1024 * 1024)) if disk_io else 0.0,
            disk_io_write_mb=(disk_io.write_bytes / (1024 * 1024)) if disk_io else 0.0,
            network_sent_mb=(net_io.bytes_sent / (1024 * 1024)) if net_io else 0.0,
            network_recv_mb=(net_io.bytes_recv / (1024 * 1024)) if net_io else 0.0,
        )

        log.info("baseline_metrics_captured", metrics=baseline)
        return baseline

    def collect_metrics(self) -> ResourceMetrics:
        """
        Collect current system resource metrics.

        Returns:
            ResourceMetrics: Current metrics snapshot
        """
        # CPU (percentage over 1 second interval)
        cpu_percent = psutil.cpu_percent(interval=0.5)

        # Memory
        memory = psutil.virtual_memory()

        # Disk I/O
        disk_io = psutil.disk_io_counters()

        # Network I/O
        net_io = psutil.net_io_counters()

        metrics = ResourceMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            available_memory_mb=memory.available / (1024 * 1024),
            disk_io_read_mb=(disk_io.read_bytes / (1024 * 1024)) if disk_io else 0.0,
            disk_io_write_mb=(disk_io.write_bytes / (1024 * 1024)) if disk_io else 0.0,
            network_sent_mb=(net_io.bytes_sent / (1024 * 1024)) if net_io else 0.0,
            network_recv_mb=(net_io.bytes_recv / (1024 * 1024)) if net_io else 0.0,
        )

        # Add to history
        self.metrics_history.append(metrics)
        if len(self.metrics_history) > self.max_history_size:
            self.metrics_history.pop(0)

        log.debug(
            "metrics_collected",
            cpu=cpu_percent,
            memory=memory.percent,
            available_memory_mb=metrics.available_memory_mb,
        )

        return metrics

    def get_average_metrics(self, last_n: int = 5) -> ResourceMetrics | None:
        """
        Get average metrics over last N measurements.

        Args:
            last_n: Number of recent measurements to average

        Returns:
            ResourceMetrics: Average metrics, or None if insufficient history
        """
        if len(self.metrics_history) < last_n:
            return None

        recent = self.metrics_history[-last_n:]

        avg_metrics = ResourceMetrics(
            timestamp=time.time(),
            cpu_percent=sum(m.cpu_percent for m in recent) / len(recent),
            memory_percent=sum(m.memory_percent for m in recent) / len(recent),
            available_memory_mb=sum(m.available_memory_mb for m in recent) / len(recent),
            disk_io_read_mb=sum(m.disk_io_read_mb for m in recent) / len(recent),
            disk_io_write_mb=sum(m.disk_io_write_mb for m in recent) / len(recent),
            network_sent_mb=sum(m.network_sent_mb for m in recent) / len(recent),
            network_recv_mb=sum(m.network_recv_mb for m in recent) / len(recent),
        )

        return avg_metrics

    def recommended_workers(self, current_metrics: ResourceMetrics | None = None) -> int:
        """
        Calculate recommended worker count based on resource utilization.

        Args:
            current_metrics: Optional current metrics (will collect if not provided)

        Returns:
            int: Recommended worker count (bounded by min/max)

        Algorithm:
        1. Back off if CPU or memory is too high
        2. Increase if resources are available and not saturated
        3. Apply smoothing to avoid thrashing
        """
        if current_metrics is None:
            current_metrics = self.collect_metrics()

        # Get average metrics for smoother decisions
        avg_metrics = self.get_average_metrics(last_n=3)
        if avg_metrics:
            decision_metrics = avg_metrics
        else:
            decision_metrics = current_metrics

        cpu = decision_metrics.cpu_percent
        memory = decision_metrics.memory_percent
        available_memory_mb = decision_metrics.available_memory_mb

        log.debug(
            "calculating_recommendation",
            current_workers=self.current_workers,
            cpu=cpu,
            memory=memory,
            available_memory_mb=available_memory_mb,
        )

        # RULE 1: Back off if overloaded (priority)
        if cpu > self.max_cpu_percent or memory > self.max_memory_percent:
            # Aggressive backoff
            recommended = max(self.min_workers, self.current_workers - 2)
            log.info(
                "resource_overload_backing_off",
                cpu=cpu,
                memory=memory,
                current_workers=self.current_workers,
                recommended=recommended,
            )
            return recommended

        # RULE 2: Back off if memory is getting low (< 1GB available)
        if available_memory_mb < 1024:
            recommended = max(self.min_workers, self.current_workers - 1)
            log.info(
                "low_memory_backing_off",
                available_memory_mb=available_memory_mb,
                current_workers=self.current_workers,
                recommended=recommended,
            )
            return recommended

        # RULE 3: Increase if resources are available
        if cpu < self.target_cpu_percent and memory < (self.max_memory_percent - 15):
            # Conservative increase (one at a time)
            recommended = min(self.max_workers, self.current_workers + 1)
            log.info(
                "resources_available_increasing",
                cpu=cpu,
                memory=memory,
                current_workers=self.current_workers,
                recommended=recommended,
            )
            return recommended

        # RULE 4: Maintain current level if in acceptable range
        log.debug(
            "maintaining_current_workers",
            current_workers=self.current_workers,
            cpu=cpu,
            memory=memory,
        )
        return self.current_workers

    def should_adjust_workers(self) -> bool:
        """
        Check if enough time has passed since last adjustment.

        Returns:
            bool: True if adjustment interval has elapsed
        """
        elapsed = time.time() - self.last_adjustment_time
        return elapsed >= self.adjustment_interval

    def update_worker_count(self, new_count: int) -> None:
        """
        Update tracked worker count and reset adjustment timer.

        Args:
            new_count: New worker count
        """
        if new_count != self.current_workers:
            log.info(
                "worker_count_updated",
                old_count=self.current_workers,
                new_count=new_count,
            )

        self.current_workers = new_count
        self.last_adjustment_time = time.time()

    def record_db2_latency(self, latency_ms: float) -> None:
        """
        Record DB2 query latency for performance tracking.

        Args:
            latency_ms: Query latency in milliseconds
        """
        if self.metrics_history:
            self.metrics_history[-1].db2_avg_latency_ms = latency_ms

        log.debug("db2_latency_recorded", latency_ms=latency_ms)

    def record_postgres_throughput(self, rows_per_sec: float) -> None:
        """
        Record PostgreSQL load throughput for performance tracking.

        Args:
            rows_per_sec: Rows loaded per second
        """
        if self.metrics_history:
            self.metrics_history[-1].postgres_throughput_rows_per_sec = rows_per_sec

        log.debug("postgres_throughput_recorded", rows_per_sec=rows_per_sec)

    def get_performance_summary(self) -> dict[str, Any]:
        """
        Get summary of performance metrics.

        Returns:
            dict: Performance summary
        """
        if not self.metrics_history:
            return {}

        recent_metrics = self.metrics_history[-10:] if len(self.metrics_history) >= 10 else self.metrics_history

        avg_cpu = sum(m.cpu_percent for m in recent_metrics) / len(recent_metrics)
        avg_memory = sum(m.memory_percent for m in recent_metrics) / len(recent_metrics)

        # DB2 latencies
        db2_latencies = [m.db2_avg_latency_ms for m in recent_metrics if m.db2_avg_latency_ms]
        avg_db2_latency = sum(db2_latencies) / len(db2_latencies) if db2_latencies else None

        # PostgreSQL throughput
        pg_throughputs = [m.postgres_throughput_rows_per_sec for m in recent_metrics if m.postgres_throughput_rows_per_sec]
        avg_pg_throughput = sum(pg_throughputs) / len(pg_throughputs) if pg_throughputs else None

        summary = {
            "avg_cpu_percent": avg_cpu,
            "avg_memory_percent": avg_memory,
            "current_workers": self.current_workers,
            "avg_db2_latency_ms": avg_db2_latency,
            "avg_postgres_throughput": avg_pg_throughput,
            "measurements": len(self.metrics_history),
        }

        return summary

    def format_performance_summary(self) -> str:
        """
        Format performance summary as human-readable string.

        Returns:
            str: Formatted summary
        """
        summary = self.get_performance_summary()

        lines = [
            "Performance Summary:",
            f"  CPU: {summary.get('avg_cpu_percent', 0):.1f}%",
            f"  Memory: {summary.get('avg_memory_percent', 0):.1f}%",
            f"  Workers: {summary.get('current_workers', 0)}",
        ]

        if summary.get("avg_db2_latency_ms"):
            lines.append(f"  DB2 Latency: {summary['avg_db2_latency_ms']:.1f}ms")

        if summary.get("avg_postgres_throughput"):
            lines.append(f"  PostgreSQL: {summary['avg_postgres_throughput']:.0f} rows/sec")

        return "\n".join(lines)


def test_resource_monitor() -> None:
    """Test resource monitor with simulated metrics."""
    monitor = ResourceMonitor(min_workers=2, max_workers=10)

    print("Testing Resource Monitor")
    print("=" * 60)

    # Collect metrics a few times
    for i in range(5):
        metrics = monitor.collect_metrics()
        print(f"\nMeasurement {i + 1}:")
        print(f"  CPU: {metrics.cpu_percent:.1f}%")
        print(f"  Memory: {metrics.memory_percent:.1f}%")
        print(f"  Available Memory: {metrics.available_memory_mb:.0f}MB")

        # Get recommendation
        recommended = monitor.recommended_workers()
        print(f"  Current Workers: {monitor.current_workers}")
        print(f"  Recommended Workers: {recommended}")

        # Update if changed
        if recommended != monitor.current_workers:
            monitor.update_worker_count(recommended)

        time.sleep(1)

    # Print summary
    print("\n" + "=" * 60)
    print(monitor.format_performance_summary())


if __name__ == "__main__":
    test_resource_monitor()
