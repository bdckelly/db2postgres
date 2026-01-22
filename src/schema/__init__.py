"""
PeopleSoft schema extraction and conversion package.

Provides tools for extracting schema from DB2 catalog tables and converting to PostgreSQL DDL.
"""

from src.schema.converter import (
    PostgresFieldDefinition,
    PostgresTableDefinition,
    SchemaConverter,
)
from src.schema.extractor import FieldDefinition, SchemaExtractor, TableDefinition
from src.schema.generator import DDLGenerator

__all__ = [
    # Extractor
    "SchemaExtractor",
    "TableDefinition",
    "FieldDefinition",
    # Converter
    "SchemaConverter",
    "PostgresTableDefinition",
    "PostgresFieldDefinition",
    # Generator
    "DDLGenerator",
]