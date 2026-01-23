"""
PeopleSoft schema extraction from catalog tables.

Extracts table and field definitions from PSRECDEFN, PSRECFIELD, and PSDBFIELD.
Handles PeopleSoft-specific features like subrecords, truncated names, and special types.
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

from config.db2_connection import get_db2_cursor
from config.settings import DB2Settings

log = structlog.get_logger()


def _jdbc_value_to_python(value: Any) -> Any:
    """
    Convert JDBC value to Python type.

    JDBC drivers return Java objects (java.lang.Integer, java.lang.String, etc.)
    which need to be converted to Python types.

    Args:
        value: Value from JDBC cursor (may be Java object or None)

    Returns:
        Python equivalent (str, int, float, None, etc.)
    """
    if value is None:
        return None

    # Get the string representation to check Java type
    value_str = str(type(value))

    # Handle Java Integer types
    if 'java.lang.Integer' in value_str or 'java.lang.Long' in value_str or 'java.lang.Short' in value_str:
        return int(str(value))

    # Handle Java floating point types
    if 'java.lang.Double' in value_str or 'java.lang.Float' in value_str:
        return float(str(value))

    # Handle Java String
    if 'java.lang.String' in value_str:
        return str(value).strip()

    # Handle Java Boolean
    if 'java.lang.Boolean' in value_str:
        return bool(value)

    # For native Python types or unknown types, return as-is
    # This handles cases where we're using ibm_db or mock connection
    if isinstance(value, str):
        return value.strip()

    return value


@dataclass
class FieldDefinition:
    """Definition of a table field from PeopleSoft catalog."""

    field_name: str
    field_type: int  # PSFieldType from ps_types
    length: int | None = None
    decimal_pos: int | None = None
    field_num: int = 0  # Position in record
    is_key: bool = False
    use_edit: str | None = None
    subrecord: str | None = None

    def __repr__(self) -> str:
        return (
            f"FieldDefinition(field_name='{self.field_name}', "
            f"field_type={self.field_type}, length={self.length})"
        )


@dataclass
class TableDefinition:
    """Definition of a table from PeopleSoft catalog."""

    record_name: str  # PeopleSoft record name (e.g., VOUCHER)
    sql_table_name: str  # Actual DB2 table name (e.g., PS_VOUCHER)
    description: str | None = None
    fields: list[FieldDefinition] = field(default_factory=list)
    key_fields: list[str] = field(default_factory=list)
    record_type: int = 0  # 0 = SQL Table, 1 = SQL View, 2 = Derived/Work, etc.

    def get_field(self, field_name: str) -> FieldDefinition | None:
        """Get field definition by name."""
        for f in self.fields:
            if f.field_name.upper() == field_name.upper():
                return f
        return None

    def get_key_fields(self) -> list[FieldDefinition]:
        """Get list of key field definitions in order."""
        key_fields = []
        for field_name in self.key_fields:
            field_def = self.get_field(field_name)
            if field_def:
                key_fields.append(field_def)
        return key_fields

    def __repr__(self) -> str:
        return (
            f"TableDefinition(record_name='{self.record_name}', "
            f"sql_table_name='{self.sql_table_name}', fields={len(self.fields)})"
        )


class SchemaExtractor:
    """
    Extract PeopleSoft schema from catalog tables.

    Queries PSRECDEFN, PSRECFIELD, and PSDBFIELD to build complete table definitions.
    """

    def __init__(self, db2_settings: DB2Settings):
        """
        Initialize schema extractor.

        Args:
            db2_settings: DB2 connection settings
        """
        self.db2_settings = db2_settings
        log.info("schema_extractor_initialized")

    def extract_table_list(self, table_names: list[str] | None = None) -> list[str]:
        """
        Extract list of SQL tables from PSRECDEFN.

        Args:
            table_names: Optional list of specific table names to extract.
                        If None, extracts all SQL tables.

        Returns:
            list[str]: List of record names

        Example:
            >>> extractor = SchemaExtractor(db2_settings)
            >>> tables = extractor.extract_table_list()
            >>> print(len(tables))
            1847
        """
        log.info("extracting_table_list", specific_tables=table_names is not None)

        query = """
            SELECT RECNAME
            FROM PSRECDEFN
            WHERE RECTYPE = 0
        """

        # Add filter for specific tables if provided
        if table_names:
            placeholders = ",".join(["?" for _ in table_names])
            query += f" AND RECNAME IN ({placeholders})"

        query += " ORDER BY RECNAME"

        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                if table_names:
                    cursor.execute(query + " WITH UR", tuple(table_names))
                else:
                    cursor.execute(query + " WITH UR")

                rows = cursor.fetchall()
                table_list = [_jdbc_value_to_python(row[0]) for row in rows]

                log.info("table_list_extracted", count=len(table_list))
                return table_list

        except Exception as e:
            log.error("table_list_extraction_failed", error=str(e))
            raise

    def extract_table_metadata(self, record_name: str) -> dict[str, Any]:
        """
        Extract basic table metadata from PSRECDEFN.

        Args:
            record_name: PeopleSoft record name

        Returns:
            dict: Table metadata including sql_table_name, description, etc.
        """
        log.debug("extracting_table_metadata", record_name=record_name)

        query = """
            SELECT RECNAME, SQLTABLENAME, RECDESCR, RECTYPE
            FROM PSRECDEFN
            WHERE RECNAME = ?
            WITH UR
        """

        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                cursor.execute(query, (record_name,))
                row = cursor.fetchone()

                if not row:
                    log.warning("table_metadata_not_found", record_name=record_name)
                    return {}

                # Extract values
                rec_name = _jdbc_value_to_python(row[0])
                sql_table = _jdbc_value_to_python(row[1])
                descr = _jdbc_value_to_python(row[2])
                rec_type = _jdbc_value_to_python(row[3])

                # Use PS_{record_name} if sql_table_name is empty
                if not sql_table or sql_table.strip() == "":
                    sql_table = f"PS_{rec_name}"

                metadata = {
                    "record_name": rec_name,
                    "sql_table_name": sql_table,
                    "description": descr if descr else None,
                    "record_type": rec_type,
                }

                log.debug("table_metadata_extracted", record_name=record_name)
                return metadata

        except Exception as e:
            log.error("table_metadata_extraction_failed", record_name=record_name, error=str(e))
            raise

    def extract_field_list(self, record_name: str) -> list[tuple[str, int, str | None, str | None]]:
        """
        Extract field list from PSRECFIELD.

        Args:
            record_name: PeopleSoft record name

        Returns:
            list: List of tuples (field_name, field_num, use_edit, subrecord)
        """
        log.debug("extracting_field_list", record_name=record_name)

        query = """
            SELECT FIELDNAME, FIELDNUM, USEEDIT, SUBRECORD
            FROM PSRECFIELD
            WHERE RECNAME = ?
            ORDER BY FIELDNUM
            WITH UR
        """

        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                cursor.execute(query, (record_name,))
                rows = cursor.fetchall()

                fields = [
                    (
                        _jdbc_value_to_python(row[0]),
                        _jdbc_value_to_python(row[1]),
                        _jdbc_value_to_python(row[2]) if row[2] else None,
                        _jdbc_value_to_python(row[3]) if row[3] else None,
                    )
                    for row in rows
                ]

                log.debug("field_list_extracted", record_name=record_name, count=len(fields))
                return fields

        except Exception as e:
            log.error("field_list_extraction_failed", record_name=record_name, error=str(e))
            raise

    def extract_field_details(self, field_name: str) -> dict[str, Any]:
        """
        Extract physical field details from PSDBFIELD.

        Args:
            field_name: Field name

        Returns:
            dict: Field details including type, length, precision
        """
        log.debug("extracting_field_details", field_name=field_name)

        query = """
            SELECT FIELDNAME, FIELDTYPE, LENGTH, DECIMALPOS
            FROM PSDBFIELD
            WHERE FIELDNAME = ?
            WITH UR
        """

        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                cursor.execute(query, (field_name,))
                row = cursor.fetchone()

                if not row:
                    log.warning("field_details_not_found", field_name=field_name)
                    return {}

                details = {
                    "field_name": _jdbc_value_to_python(row[0]),
                    "field_type": _jdbc_value_to_python(row[1]),
                    "length": _jdbc_value_to_python(row[2]) if row[2] else None,
                    "decimal_pos": _jdbc_value_to_python(row[3]) if row[3] else None,
                }

                log.debug("field_details_extracted", field_name=field_name)
                return details

        except Exception as e:
            log.error("field_details_extraction_failed", field_name=field_name, error=str(e))
            raise

    def extract_key_fields(self, record_name: str) -> list[str]:
        """
        Extract key fields from PSRECFIELD.

        Args:
            record_name: PeopleSoft record name

        Returns:
            list[str]: List of key field names in order
        """
        log.debug("extracting_key_fields", record_name=record_name)

        query = """
            SELECT FIELDNAME
            FROM PSRECFIELD
            WHERE RECNAME = ?
            AND USEEDIT = 1
            ORDER BY FIELDNUM
            WITH UR
        """

        try:
            with get_db2_cursor(self.db2_settings) as cursor:
                cursor.execute(query, (record_name,))
                rows = cursor.fetchall()

                key_fields = [_jdbc_value_to_python(row[0]) for row in rows]

                log.debug("key_fields_extracted", record_name=record_name, count=len(key_fields))
                return key_fields

        except Exception as e:
            log.error("key_fields_extraction_failed", record_name=record_name, error=str(e))
            # Don't raise - some tables might not have keys defined
            return []

    def extract_table_definition(self, record_name: str) -> TableDefinition:
        """
        Extract complete table definition including all fields.

        Args:
            record_name: PeopleSoft record name

        Returns:
            TableDefinition: Complete table definition

        Example:
            >>> extractor = SchemaExtractor(db2_settings)
            >>> table_def = extractor.extract_table_definition("VOUCHER")
            >>> print(table_def.sql_table_name)
            PS_VOUCHER
            >>> print(len(table_def.fields))
            42
        """
        log.info("extracting_table_definition", record_name=record_name)

        try:
            # Get table metadata
            metadata = self.extract_table_metadata(record_name)
            if not metadata:
                raise ValueError(f"Table {record_name} not found in PSRECDEFN")

            # Get field list
            field_list = self.extract_field_list(record_name)

            # Get key fields
            key_fields = self.extract_key_fields(record_name)

            # Build field definitions
            fields = []
            for field_name, field_num, use_edit, subrecord in field_list:
                # Get physical field details from PSDBFIELD
                field_details = self.extract_field_details(field_name)

                if not field_details:
                    log.warning(
                        "field_details_missing",
                        record_name=record_name,
                        field_name=field_name,
                    )
                    continue

                field_def = FieldDefinition(
                    field_name=field_name,
                    field_type=field_details.get("field_type", 0),
                    length=field_details.get("length"),
                    decimal_pos=field_details.get("decimal_pos"),
                    field_num=field_num,
                    is_key=(field_name in key_fields),
                    use_edit=use_edit,
                    subrecord=subrecord,
                )
                fields.append(field_def)

            # Create table definition
            table_def = TableDefinition(
                record_name=metadata["record_name"],
                sql_table_name=metadata["sql_table_name"],
                description=metadata.get("description"),
                fields=fields,
                key_fields=key_fields,
                record_type=metadata.get("record_type", 0),
            )

            log.info(
                "table_definition_extracted",
                record_name=record_name,
                fields=len(fields),
                keys=len(key_fields),
            )
            return table_def

        except Exception as e:
            log.error("table_definition_extraction_failed", record_name=record_name, error=str(e))
            raise

    def extract_all_tables(
        self, table_names: list[str] | None = None
    ) -> dict[str, TableDefinition]:
        """
        Extract definitions for all tables (or specified subset).

        Args:
            table_names: Optional list of specific tables to extract

        Returns:
            dict[str, TableDefinition]: Dictionary mapping record name to table definition

        Example:
            >>> extractor = SchemaExtractor(db2_settings)
            >>> tables = extractor.extract_all_tables(["VOUCHER", "VCHR_LINE"])
            >>> print(len(tables))
            2
        """
        log.info("extracting_all_tables", specific_tables=table_names is not None)

        try:
            # Get table list
            if table_names:
                tables_to_extract = table_names
            else:
                tables_to_extract = self.extract_table_list()

            # Extract each table
            table_definitions = {}
            for i, record_name in enumerate(tables_to_extract, 1):
                try:
                    log.info(
                        "extracting_table",
                        record_name=record_name,
                        progress=f"{i}/{len(tables_to_extract)}",
                    )
                    table_def = self.extract_table_definition(record_name)
                    table_definitions[record_name] = table_def

                except Exception as e:
                    log.error(
                        "table_extraction_failed",
                        record_name=record_name,
                        error=str(e),
                    )
                    # Continue with next table
                    continue

            log.info("all_tables_extracted", success_count=len(table_definitions))
            return table_definitions

        except Exception as e:
            log.error("all_tables_extraction_failed", error=str(e))
            raise
