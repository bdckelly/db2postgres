"""
Schema converter from DB2 to PostgreSQL.

Converts PeopleSoft table definitions from DB2 types to PostgreSQL types.
Handles PeopleSoft-specific quirks and creates PostgreSQL-compatible definitions.
"""

from dataclasses import dataclass, field

import structlog

from src.schema.extractor import FieldDefinition, TableDefinition
from src.utils.ps_types import (
    PSFieldType,
    convert_db2_type_to_postgres,
    is_effdt_field,
)

log = structlog.get_logger()


# DB2 type names by PeopleSoft field type
PS_TYPE_TO_DB2: dict[int, str] = {
    PSFieldType.CHAR: "CHAR",
    PSFieldType.LONG_CHAR: "VARCHAR",
    PSFieldType.NUMBER: "DECIMAL",
    PSFieldType.SIGNED_NUMBER: "DECIMAL",
    PSFieldType.DATE: "DATE",
    PSFieldType.TIME: "TIME",
    PSFieldType.DATETIME: "TIMESTAMP",
    PSFieldType.IMAGE: "BLOB",
    PSFieldType.IMAGE_REFERENCE: "VARCHAR",
}


@dataclass
class PostgresFieldDefinition:
    """PostgreSQL field definition."""

    field_name: str
    postgres_type: str
    is_nullable: bool = True
    is_key: bool = False
    default_value: str | None = None
    comment: str | None = None

    def to_ddl(self) -> str:
        """
        Generate DDL fragment for this field.

        Returns:
            str: DDL fragment (e.g., 'business_unit VARCHAR(5) NOT NULL')
        """
        ddl = f"{self.field_name.lower()} {self.postgres_type}"

        if not self.is_nullable:
            ddl += " NOT NULL"

        if self.default_value:
            ddl += f" DEFAULT {self.default_value}"

        return ddl


@dataclass
class PostgresTableDefinition:
    """PostgreSQL table definition."""

    table_name: str  # Lowercase PostgreSQL table name
    fields: list[PostgresFieldDefinition] = field(default_factory=list)
    primary_key_fields: list[str] = field(default_factory=list)
    indexes: list[tuple[str, list[str]]] = field(default_factory=list)  # (index_name, columns)
    comment: str | None = None

    def get_field(self, field_name: str) -> PostgresFieldDefinition | None:
        """Get field by name (case-insensitive)."""
        field_name_lower = field_name.lower()
        for f in self.fields:
            if f.field_name.lower() == field_name_lower:
                return f
        return None


class SchemaConverter:
    """
    Convert PeopleSoft/DB2 schema to PostgreSQL schema.

    Handles type mappings, naming conventions, and PostgreSQL-specific optimizations.
    """

    def __init__(self, convert_effdt_nulls: bool = False, use_lowercase: bool = True):
        """
        Initialize schema converter.

        Args:
            convert_effdt_nulls: If True, convert EFFDT 1900-01-01 to NULL
            use_lowercase: If True, convert table/column names to lowercase
        """
        self.convert_effdt_nulls = convert_effdt_nulls
        self.use_lowercase = use_lowercase
        log.info(
            "schema_converter_initialized",
            convert_effdt_nulls=convert_effdt_nulls,
            use_lowercase=use_lowercase,
        )

    def convert_field_name(self, field_name: str) -> str:
        """
        Convert field name to PostgreSQL naming convention.

        Args:
            field_name: PeopleSoft field name (e.g., BUSINESS_UNIT)

        Returns:
            str: PostgreSQL field name (e.g., business_unit)
        """
        if self.use_lowercase:
            return field_name.lower()
        return field_name

    def convert_table_name(self, sql_table_name: str) -> str:
        """
        Convert table name to PostgreSQL naming convention.

        Args:
            sql_table_name: DB2 table name (e.g., PS_VOUCHER)

        Returns:
            str: PostgreSQL table name (e.g., ps_voucher)
        """
        if self.use_lowercase:
            return sql_table_name.lower()
        return sql_table_name

    def get_db2_type_name(self, ps_field_type: int) -> str:
        """
        Get DB2 type name from PeopleSoft field type.

        Args:
            ps_field_type: PeopleSoft field type constant

        Returns:
            str: DB2 type name
        """
        return PS_TYPE_TO_DB2.get(ps_field_type, "VARCHAR")

    def convert_field(
        self, field_def: FieldDefinition, is_key: bool = False
    ) -> PostgresFieldDefinition:
        """
        Convert single field definition from DB2 to PostgreSQL.

        Args:
            field_def: Source field definition
            is_key: Whether field is part of primary key

        Returns:
            PostgresFieldDefinition: PostgreSQL field definition
        """
        log.debug("converting_field", field_name=field_def.field_name, field_type=field_def.field_type)

        # Get DB2 type name
        db2_type = self.get_db2_type_name(field_def.field_type)

        # Convert to PostgreSQL type
        postgres_type = convert_db2_type_to_postgres(
            db2_type=db2_type,
            length=field_def.length,
            precision=field_def.length,
            scale=field_def.decimal_pos,
        )

        # Determine nullability
        # Key fields are typically NOT NULL in PeopleSoft
        is_nullable = not is_key

        # Special handling for EFFDT fields
        default_value = None
        if is_effdt_field(field_def.field_name) and self.convert_effdt_nulls:
            # Add comment about EFFDT null convention
            comment = "PeopleSoft uses 1900-01-01 for null dates"
        else:
            comment = None

        pg_field = PostgresFieldDefinition(
            field_name=self.convert_field_name(field_def.field_name),
            postgres_type=postgres_type,
            is_nullable=is_nullable,
            is_key=is_key,
            default_value=default_value,
            comment=comment,
        )

        log.debug(
            "field_converted",
            field_name=field_def.field_name,
            db2_type=db2_type,
            postgres_type=postgres_type,
        )

        return pg_field

    def convert_table(self, table_def: TableDefinition) -> PostgresTableDefinition:
        """
        Convert complete table definition from DB2 to PostgreSQL.

        Args:
            table_def: Source table definition

        Returns:
            PostgresTableDefinition: PostgreSQL table definition
        """
        log.info("converting_table", table_name=table_def.sql_table_name, fields=len(table_def.fields))

        # Convert table name
        pg_table_name = self.convert_table_name(table_def.sql_table_name)

        # Convert fields
        pg_fields = []
        for field_def in table_def.fields:
            is_key = field_def.field_name in table_def.key_fields
            pg_field = self.convert_field(field_def, is_key=is_key)
            pg_fields.append(pg_field)

        # Convert primary key field names
        pg_key_fields = [self.convert_field_name(f) for f in table_def.key_fields]

        # Generate table comment from description
        comment = table_def.description

        pg_table = PostgresTableDefinition(
            table_name=pg_table_name,
            fields=pg_fields,
            primary_key_fields=pg_key_fields,
            indexes=[],  # Indexes will be added separately
            comment=comment,
        )

        log.info(
            "table_converted",
            table_name=pg_table_name,
            fields=len(pg_fields),
            keys=len(pg_key_fields),
        )

        return pg_table

    def convert_all_tables(
        self, table_definitions: dict[str, TableDefinition]
    ) -> dict[str, PostgresTableDefinition]:
        """
        Convert all table definitions from DB2 to PostgreSQL.

        Args:
            table_definitions: Dictionary of source table definitions

        Returns:
            dict[str, PostgresTableDefinition]: Dictionary of PostgreSQL table definitions
        """
        log.info("converting_all_tables", count=len(table_definitions))

        pg_tables = {}
        for record_name, table_def in table_definitions.items():
            try:
                pg_table = self.convert_table(table_def)
                pg_tables[record_name] = pg_table

            except Exception as e:
                log.error(
                    "table_conversion_failed",
                    record_name=record_name,
                    error=str(e),
                )
                # Continue with next table
                continue

        log.info("all_tables_converted", success_count=len(pg_tables))
        return pg_tables

    def add_indexes_for_keys(self, pg_table: PostgresTableDefinition) -> None:
        """
        Add index for primary key fields if not already present.

        Args:
            pg_table: PostgreSQL table definition to modify
        """
        if not pg_table.primary_key_fields:
            return

        # Create index name
        index_name = f"{pg_table.table_name}_pk"

        # Add primary key index
        pg_table.indexes.append((index_name, pg_table.primary_key_fields))

        log.debug(
            "primary_key_index_added",
            table_name=pg_table.table_name,
            index_name=index_name,
            columns=pg_table.primary_key_fields,
        )

    def suggest_additional_indexes(
        self, table_def: TableDefinition, pg_table: PostgresTableDefinition
    ) -> list[tuple[str, list[str]]]:
        """
        Suggest additional indexes based on PeopleSoft conventions.

        Args:
            table_def: Original PeopleSoft table definition
            pg_table: PostgreSQL table definition

        Returns:
            list: List of (index_name, columns) tuples
        """
        suggested_indexes = []

        # Common PeopleSoft index patterns
        common_index_fields = ["EFFDT", "SETID", "BUSINESS_UNIT", "EMPLID"]

        for field_name in common_index_fields:
            if field_name in [f.field_name for f in table_def.fields]:
                # Don't index if already in primary key
                if field_name not in table_def.key_fields:
                    pg_field_name = self.convert_field_name(field_name)
                    index_name = f"{pg_table.table_name}_{pg_field_name}_idx"
                    suggested_indexes.append((index_name, [pg_field_name]))

        return suggested_indexes
