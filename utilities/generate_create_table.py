#!/usr/bin/env python3
"""
Generate DB2 CREATE TABLE statements from SYSIBM.SYSCOLUMNS query output.

Usage:
    python generate_create_table.py input.csv [table_name] [schema_name]

The input CSV should have columns: NAME, COLTYPE, LENGTH, SCALE, NULLS
Example query to generate input:
    SELECT NAME, COLTYPE, LENGTH, SCALE, NULLS
    FROM SYSIBM.SYSCOLUMNS
    WHERE TBNAME = 'PSRECDEFN'
      AND TBCREATOR = 'PSF8DEV'
    ORDER BY COLNO;
"""

import csv
import sys
from pathlib import Path


def parse_db2_type(coltype: str, length: int, scale: int) -> str:
    """
    Convert DB2 column type information to CREATE TABLE type definition.

    Args:
        coltype: DB2 column type (e.g., 'CHAR    ', 'VARCHAR ', 'DECIMAL ')
        length: Column length
        scale: Decimal scale (for numeric types)

    Returns:
        Formatted type string (e.g., 'CHAR(3)', 'DECIMAL(10,2)')
    """
    coltype = coltype.strip().upper()

    if coltype == 'CHAR' or coltype == 'CHARACTER':
        return f'CHAR({length})'
    elif coltype == 'VARCHAR' or coltype == 'VARG' or coltype == 'VARGRAPH':
        return f'VARCHAR({length})'
    elif coltype == 'LONGVAR' or coltype == 'LONG VARCHAR':
        # LONGVAR is internal name for long varchar - use CLOB for large text
        if length > 32672:
            return f'CLOB({length})'
        else:
            return f'VARCHAR({length})'
    elif coltype == 'DECIMAL' or coltype == 'NUMERIC':
        if scale > 0:
            return f'DECIMAL({length},{scale})'
        else:
            return f'DECIMAL({length})'
    elif coltype == 'INTEGER' or coltype == 'INT':
        return 'INTEGER'
    elif coltype == 'SMALLINT':
        return 'SMALLINT'
    elif coltype == 'BIGINT':
        return 'BIGINT'
    elif coltype == 'DATE':
        return 'DATE'
    elif coltype == 'TIME':
        return 'TIME'
    elif coltype == 'TIMESTAMP' or coltype == 'TIMESTMP':
        # TIMESTMP is internal name for TIMESTAMP
        return 'TIMESTAMP'
    elif coltype == 'CLOB':
        return f'CLOB({length})'
    elif coltype == 'BLOB':
        return f'BLOB({length})'
    elif coltype == 'DBCLOB':
        return f'DBCLOB({length})'
    elif coltype == 'GRAPHIC':
        return f'GRAPHIC({length})'
    elif coltype == 'REAL' or coltype == 'FLOAT':
        return 'REAL'
    elif coltype == 'DOUBLE':
        return 'DOUBLE'
    else:
        # Unknown type - return as-is with length
        return f'{coltype}({length})'


def generate_create_table(csv_file: Path, table_name: str = None, schema_name: str = None) -> str:
    """
    Generate CREATE TABLE statement from CSV file.

    Args:
        csv_file: Path to CSV file with column definitions
        table_name: Optional table name (if not provided, uses input filename)
        schema_name: Optional schema name

    Returns:
        CREATE TABLE SQL statement
    """
    # Default table name from filename if not provided
    if not table_name:
        table_name = csv_file.stem.upper()

    # Build full table identifier
    if schema_name:
        full_table_name = f'{schema_name}.{table_name}'
    else:
        full_table_name = table_name

    # Read CSV and parse columns
    columns = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row['NAME'].strip()
            coltype = row['COLTYPE'].strip()
            length = int(row['LENGTH'])
            scale = int(row['SCALE'])
            nulls = row['NULLS'].strip().upper()

            # Build column definition
            type_def = parse_db2_type(coltype, length, scale)
            null_constraint = '' if nulls == 'Y' else ' NOT NULL'

            col_def = f'    {name:<30} {type_def:<20}{null_constraint}'
            columns.append(col_def)

    # Build CREATE TABLE statement
    sql = f'CREATE TABLE {full_table_name} (\n'
    sql += ',\n'.join(columns)
    sql += '\n);'

    return sql


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nError: Missing required argument")
        print("Usage: python generate_create_table.py input.csv [table_name] [schema_name]")
        sys.exit(1)

    csv_file = Path(sys.argv[1])

    if not csv_file.exists():
        print(f"Error: File not found: {csv_file}")
        sys.exit(1)

    table_name = sys.argv[2] if len(sys.argv) > 2 else None
    schema_name = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        sql = generate_create_table(csv_file, table_name, schema_name)
        print(sql)

        # Also save to output file
        output_file = csv_file.with_suffix('.sql')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(sql)

        print(f"\n-- SQL saved to: {output_file}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
