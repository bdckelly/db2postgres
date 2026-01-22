"""
PeopleSoft type definitions and DB2 to PostgreSQL type mappings.

Contains constants for PeopleSoft field types and conversion logic for DB2 to PostgreSQL.
Handles PeopleSoft-specific quirks like EFFDT null conventions and special field types.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum
from typing import Any


class PSFieldType(IntEnum):
    """PeopleSoft field types from PSDBFIELD.FIELDTYPE."""

    CHAR = 0  # Fixed-length character
    LONG_CHAR = 1  # Variable-length character
    NUMBER = 2  # Numeric (INTEGER, DECIMAL)
    SIGNED_NUMBER = 3  # Signed numeric
    DATE = 4  # Date field
    TIME = 5  # Time field
    DATETIME = 6  # Datetime/Timestamp
    IMAGE = 8  # Binary/BLOB (PSIMAGE)
    IMAGE_REFERENCE = 9  # Image reference


# PeopleSoft special date used to represent null EFFDT (Effective Date)
PS_NULL_DATE = date(1900, 1, 1)

# PeopleSoft special datetime used to represent null timestamps
PS_NULL_DATETIME = datetime(1900, 1, 1, 0, 0, 0)


@dataclass
class TypeMapping:
    """Mapping from DB2 type to PostgreSQL type."""

    db2_type: str
    postgres_type: str
    needs_length: bool = False  # Whether type requires length specification
    needs_precision: bool = False  # Whether type requires precision/scale
    notes: str = ""


# Core type mappings from DB2 to PostgreSQL
TYPE_MAPPINGS: dict[str, TypeMapping] = {
    # Character types
    "CHAR": TypeMapping(
        db2_type="CHAR",
        postgres_type="VARCHAR",  # Use VARCHAR instead of CHAR to save space
        needs_length=True,
        notes="DB2 CHAR maps to PostgreSQL VARCHAR for better space efficiency",
    ),
    "VARCHAR": TypeMapping(
        db2_type="VARCHAR",
        postgres_type="VARCHAR",
        needs_length=True,
        notes="Direct mapping",
    ),
    "LONG VARCHAR": TypeMapping(
        db2_type="LONG VARCHAR",
        postgres_type="TEXT",
        notes="Variable length text, no length limit in PostgreSQL",
    ),
    "CLOB": TypeMapping(
        db2_type="CLOB",
        postgres_type="TEXT",
        notes="PeopleSoft PSLONGCHAR fields map to TEXT",
    ),
    # Numeric types
    "SMALLINT": TypeMapping(
        db2_type="SMALLINT",
        postgres_type="SMALLINT",
        notes="Direct mapping, 2-byte integer",
    ),
    "INTEGER": TypeMapping(
        db2_type="INTEGER",
        postgres_type="INTEGER",
        notes="Direct mapping, 4-byte integer",
    ),
    "BIGINT": TypeMapping(
        db2_type="BIGINT",
        postgres_type="BIGINT",
        notes="Direct mapping, 8-byte integer",
    ),
    "DECIMAL": TypeMapping(
        db2_type="DECIMAL",
        postgres_type="NUMERIC",
        needs_precision=True,
        notes="DECIMAL(p,s) maps to NUMERIC(p,s)",
    ),
    "NUMERIC": TypeMapping(
        db2_type="NUMERIC",
        postgres_type="NUMERIC",
        needs_precision=True,
        notes="Direct mapping",
    ),
    "REAL": TypeMapping(
        db2_type="REAL",
        postgres_type="REAL",
        notes="Single-precision float",
    ),
    "DOUBLE": TypeMapping(
        db2_type="DOUBLE",
        postgres_type="DOUBLE PRECISION",
        notes="Double-precision float",
    ),
    # Date/Time types
    "DATE": TypeMapping(
        db2_type="DATE",
        postgres_type="DATE",
        notes="Watch for 1900-01-01 as null convention in EFFDT fields",
    ),
    "TIME": TypeMapping(
        db2_type="TIME",
        postgres_type="TIME",
        notes="Time without timezone",
    ),
    "TIMESTAMP": TypeMapping(
        db2_type="TIMESTAMP",
        postgres_type="TIMESTAMP",
        notes="Timestamp without timezone",
    ),
    # Binary types
    "BLOB": TypeMapping(
        db2_type="BLOB",
        postgres_type="BYTEA",
        notes="PeopleSoft PSIMAGE fields map to BYTEA",
    ),
    "BINARY": TypeMapping(
        db2_type="BINARY",
        postgres_type="BYTEA",
        notes="Fixed-length binary data",
    ),
    "VARBINARY": TypeMapping(
        db2_type="VARBINARY",
        postgres_type="BYTEA",
        notes="Variable-length binary data",
    ),
}


def convert_db2_type_to_postgres(
    db2_type: str,
    length: int | None = None,
    precision: int | None = None,
    scale: int | None = None,
) -> str:
    """
    Convert DB2 type to PostgreSQL type with appropriate length/precision.

    Args:
        db2_type: DB2 type name (e.g., 'CHAR', 'DECIMAL', 'VARCHAR')
        length: Length for character types
        precision: Precision for numeric types
        scale: Scale for numeric types

    Returns:
        str: PostgreSQL type specification (e.g., 'VARCHAR(30)', 'NUMERIC(15,2)')

    Example:
        >>> convert_db2_type_to_postgres('CHAR', length=30)
        'VARCHAR(30)'
        >>> convert_db2_type_to_postgres('DECIMAL', precision=15, scale=2)
        'NUMERIC(15,2)'
    """
    db2_type_upper = db2_type.upper()

    if db2_type_upper not in TYPE_MAPPINGS:
        # Unknown type, log warning and use TEXT as fallback
        return "TEXT"

    mapping = TYPE_MAPPINGS[db2_type_upper]
    pg_type = mapping.postgres_type

    if mapping.needs_length and length:
        return f"{pg_type}({length})"
    elif mapping.needs_precision and precision is not None:
        if scale is not None and scale > 0:
            return f"{pg_type}({precision},{scale})"
        else:
            return f"{pg_type}({precision})"
    else:
        return pg_type


def is_effdt_field(field_name: str) -> bool:
    """
    Check if field name is an effective date field.

    PeopleSoft uses EFFDT fields with 1900-01-01 to represent null dates.

    Args:
        field_name: Field name to check

    Returns:
        bool: True if field is likely an EFFDT field
    """
    return field_name.upper() in ("EFFDT", "EFFDT_FROM", "EFFDT_TO")


def is_null_date(value: Any) -> bool:
    """
    Check if date value represents PeopleSoft null date (1900-01-01).

    Args:
        value: Date value to check

    Returns:
        bool: True if value is PS null date
    """
    if isinstance(value, date):
        return value == PS_NULL_DATE
    return False


def is_null_datetime(value: Any) -> bool:
    """
    Check if datetime value represents PeopleSoft null datetime.

    Args:
        value: Datetime value to check

    Returns:
        bool: True if value is PS null datetime
    """
    if isinstance(value, datetime):
        return value == PS_NULL_DATETIME
    return False


def convert_ps_null_date(value: Any, convert_to_null: bool = False) -> Any:
    """
    Convert PeopleSoft null date (1900-01-01) to PostgreSQL null or keep as-is.

    Args:
        value: Date value to convert
        convert_to_null: If True, convert 1900-01-01 to None; if False, keep as-is

    Returns:
        Converted value (None or original)
    """
    if convert_to_null and is_null_date(value):
        return None
    return value


# PeopleSoft table name patterns that indicate special handling
SPECIAL_TABLE_PATTERNS = {
    "TREE": "Tree structure tables with self-referential keys",
    "XLAT": "Translate value tables",
    "SETID": "Tables with SETID (multi-tenant key)",
    "AUDIT": "Audit trail tables",
    "SCRTY": "Security tables",
}


def is_special_table(table_name: str) -> str | None:
    """
    Check if table matches special PeopleSoft patterns requiring special handling.

    Args:
        table_name: PeopleSoft table name (e.g., 'PS_TREE_NODE')

    Returns:
        str | None: Description of special handling if match found, None otherwise
    """
    table_upper = table_name.upper()
    for pattern, description in SPECIAL_TABLE_PATTERNS.items():
        if pattern in table_upper:
            return description
    return None


# Common PeopleSoft key fields that are good candidates for chunking
GOOD_CHUNK_KEYS = {
    "EFFDT",  # Effective date - excellent for date-based chunking
    "EMPLID",  # Employee ID - good for numeric range chunking
    "BUSINESS_UNIT",  # Business unit - good for categorical chunking
    "SETID",  # Set ID - good for categorical chunking
    "VOUCHER_ID",  # Voucher ID - good for numeric chunking
    "INVOICE_ID",  # Invoice ID - good for numeric chunking
    "PO_ID",  # Purchase order ID - good for numeric chunking
}


def suggest_chunk_key(key_columns: list[str]) -> str | None:
    """
    Suggest best column for chunking based on PeopleSoft conventions.

    Args:
        key_columns: List of key column names for table

    Returns:
        str | None: Recommended chunk key, or None if no good candidate
    """
    # Prefer EFFDT for date-based chunking
    if "EFFDT" in key_columns:
        return "EFFDT"

    # Otherwise look for numeric IDs
    for key in key_columns:
        if key.upper() in GOOD_CHUNK_KEYS:
            return key

    # Fall back to first key column
    return key_columns[0] if key_columns else None
