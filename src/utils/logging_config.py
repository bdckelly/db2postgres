"""
Structured logging configuration using structlog.

Provides JSON output for production and colored console output for development.
Includes context processors for timestamp, log level, and process info.
"""

import logging
import sys
from pathlib import Path
from typing import Literal

import structlog
from structlog.types import EventDict, Processor


def add_process_info(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add process ID and name to log context."""
    import multiprocessing
    import os

    event_dict["pid"] = os.getpid()
    try:
        current_process = multiprocessing.current_process()
        event_dict["process_name"] = current_process.name
    except Exception:
        event_dict["process_name"] = "main"
    return event_dict


def setup_logging(
    log_level: str = "INFO",
    log_format: Literal["json", "console"] = "console",
    log_file: Path | None = None,
) -> None:
    """
    Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Output format - 'json' for production, 'console' for development
        log_file: Optional log file path for file output

    Example:
        setup_logging(log_level="INFO", log_format="console", log_file=Path("logs/migration.log"))
    """
    # Convert log level string to constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors for all formats
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_process_info,
        structlog.processors.StackInfoRenderer(),
    ]

    # Format-specific processors
    if log_format == "json":
        # JSON output for production
        processors: list[Processor] = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console output for development
        processors = shared_processors + [
            structlog.processors.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        level=numeric_level,
        stream=sys.stdout,
    )

    # Add file handler if log_file specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric_level)

        if log_format == "json":
            file_handler.setFormatter(logging.Formatter("%(message)s"))
        else:
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )

        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

    log = structlog.get_logger()
    log.info(
        "logging_configured",
        log_level=log_level,
        log_format=log_format,
        log_file=str(log_file) if log_file else None,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Optional logger name (defaults to calling module)

    Returns:
        Bound logger instance

    Example:
        log = get_logger(__name__)
        log.info("table_extraction_started", table="PS_VOUCHER", rows=1000000)
    """
    return structlog.get_logger(name)


# Convenience function for adding context to logs
def bind_context(**kwargs) -> None:
    """
    Bind context variables that will be included in all subsequent log entries.

    Args:
        **kwargs: Key-value pairs to add to log context

    Example:
        bind_context(table="PS_VOUCHER", worker_id=3)
        log.info("extraction_started")  # Will include table and worker_id
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_context(*keys: str) -> None:
    """
    Remove context variables from log context.

    Args:
        *keys: Keys to remove from context

    Example:
        unbind_context("table", "worker_id")
    """
    structlog.contextvars.unbind_contextvars(*keys)


def clear_context() -> None:
    """
    Clear all context variables from log context.

    Example:
        clear_context()  # Remove all bound context
    """
    structlog.contextvars.clear_contextvars()
