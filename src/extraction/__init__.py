"""
Data extraction package for DB2 to PostgreSQL migration.

Provides chunking strategies, cursor management, and CSV serialization.
"""

from src.extraction.chunker import (
    ChunkDefinition,
    ChunkStrategy,
    DateChunkStrategy,
    NoChunkStrategy,
    NumericRangeStrategy,
    OffsetLimitStrategy,
    TableSize,
    determine_chunking_strategy,
    determine_table_size,
)
from src.extraction.cursor import ExtractionCursor, extract_table_sample
from src.extraction.serializer import (
    BinaryFileSerializer,
    PostgresCopySerializer,
    get_staging_file_path,
    validate_csv_file,
)

__all__ = [
    # Chunking
    "ChunkDefinition",
    "ChunkStrategy",
    "NoChunkStrategy",
    "DateChunkStrategy",
    "NumericRangeStrategy",
    "OffsetLimitStrategy",
    "TableSize",
    "determine_chunking_strategy",
    "determine_table_size",
    # Cursors
    "ExtractionCursor",
    "extract_table_sample",
    # Serialization
    "PostgresCopySerializer",
    "BinaryFileSerializer",
    "get_staging_file_path",
    "validate_csv_file",
]