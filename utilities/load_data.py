#!/usr/bin/env python3
"""
Quick script to load extracted CSV data into PostgreSQL.

Usage:
    python load_data.py
"""

import sys
from pathlib import Path

import psycopg

from config.settings import get_settings

def load_table(pg_conn, staging_dir: Path, table_name: str, pg_table_name: str):
    """Load CSV data into PostgreSQL table."""
    csv_file = staging_dir / table_name / "data.csv"

    if not csv_file.exists():
        print(f"❌ CSV file not found: {csv_file}")
        return False

    print(f"  📂 Loading {table_name}...")

    try:
        with pg_conn.cursor() as cursor:
            # Use COPY to bulk load (psycopg3 syntax)
            print(f"     Opening CSV file...")
            with open(csv_file, 'r', encoding='utf-8') as f:
                print(f"     Running COPY command...")
                # psycopg3 uses copy() instead of copy_from()
                with cursor.copy(f"COPY {pg_table_name} FROM STDIN WITH (FORMAT CSV, DELIMITER ',', NULL '\\N')") as copy:
                    for line in f:
                        copy.write(line)

            print(f"     COPY complete, committing...")
            pg_conn.commit()

            # Get row count
            print(f"     Verifying row count...")
            cursor.execute(f"SELECT COUNT(*) FROM {pg_table_name}")
            count = cursor.fetchone()[0]

            print(f"  ✓ Loaded {count} rows into {pg_table_name}\n")
            return True

    except Exception as e:
        pg_conn.rollback()
        print(f"  ❌ Error loading {pg_table_name}: {e}\n")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    settings = get_settings()

    print("\n🔄 Loading data into PostgreSQL...\n")

    # Import connection utilities with SSH tunnel support
    from config.postgres_connection import create_connection, stop_ssh_tunnel

    # Connect to PostgreSQL (will auto-start SSH tunnel if enabled)
    try:
        if settings.ssh_tunnel.tunnel_enabled:
            print(f"📡 Starting SSH tunnel to {settings.ssh_tunnel.host}...")

        pg_conn = create_connection(settings.postgres, settings.ssh_tunnel)
        print(f"✓ Connected to PostgreSQL: {settings.postgres.database}\n")

        if settings.ssh_tunnel.tunnel_enabled:
            print(f"✓ SSH tunnel active\n")

    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        return 1

    try:
        staging_dir = settings.migration.staging_dir

        # Load tables
        tables = [
            ("PS_BUS_UNIT_TBL_FS", "ps_bus_unit_tbl_fs"),
            ("PS_INSTALLATION", "ps_installation"),
        ]

        success_count = 0
        for db2_name, pg_name in tables:
            if load_table(pg_conn, staging_dir, db2_name, pg_name):
                success_count += 1

        print(f"\n✓ Loaded {success_count}/{len(tables)} tables successfully")

        return 0 if success_count == len(tables) else 1

    finally:
        pg_conn.close()

        # Clean up SSH tunnel
        if settings.ssh_tunnel.tunnel_enabled:
            stop_ssh_tunnel()
            print("\n✓ SSH tunnel stopped")


if __name__ == "__main__":
    sys.exit(main())
