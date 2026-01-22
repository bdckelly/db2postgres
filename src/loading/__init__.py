"""
PostgreSQL data loading and validation package.

Provides bulk loading with COPY and data validation.
"""

from src.loading.bulk_loader import BulkLoadError, BulkLoader, load_table
from src.loading.validator import (
    DataValidator,
    ValidationMode,
    ValidationResult,
    print_validation_report,
)

__all__ = [
    # Bulk loading
    "BulkLoader",
    "BulkLoadError",
    "load_table",
    # Validation
    "DataValidator",
    "ValidationMode",
    "ValidationResult",
    "print_validation_report",
]