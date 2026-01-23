"""
PeopleSoft schema extraction and conversion package.

Provides tools for extracting schema from DB2 catalog tables and converting to PostgreSQL DDL.
"""

from src.schema.converter import (
    PostgresFieldDefinition,
    PostgresTableDefinition,
    PostgresViewDefinition,
    SchemaConverter,
    ViewSQLConverter,
)
from src.schema.extractor import (
    FieldDefinition,
    SchemaExtractor,
    TableDefinition,
    ViewDefinition,
)
from src.schema.generator import DDLGenerator

__all__ = [
    # Extractor
    "SchemaExtractor",
    "TableDefinition",
    "FieldDefinition",
    "ViewDefinition",
    # Converter
    "SchemaConverter",
    "PostgresTableDefinition",
    "PostgresFieldDefinition",
    "PostgresViewDefinition",
    "ViewSQLConverter",
    # Generator
    "DDLGenerator",
]