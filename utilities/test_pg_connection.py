#!/usr/bin/env python3
"""Test PostgreSQL connection with SSH tunnel support."""

import sys

print("1. Testing imports...")
try:
    import psycopg
    print("   ✓ psycopg imported")
except Exception as e:
    print(f"   ❌ Failed to import psycopg: {e}")
    sys.exit(1)

try:
    from sshtunnel import SSHTunnelForwarder
    print("   ✓ sshtunnel imported")
except ImportError:
    print("   ⚠ sshtunnel not installed - SSH tunnel support unavailable")
    print("     Install with: pip install sshtunnel")

print("\n2. Testing settings...")
try:
    from config.settings import get_settings
    settings = get_settings()
    print(f"   ✓ Settings loaded")
    print(f"   PostgreSQL: {settings.postgres.host}:{settings.postgres.port}/{settings.postgres.database}")
    if settings.ssh_tunnel.tunnel_enabled:
        print(f"   SSH Tunnel: {settings.ssh_tunnel.user}@{settings.ssh_tunnel.host}:{settings.ssh_tunnel.port}")
    else:
        print(f"   SSH Tunnel: Disabled")
except Exception as e:
    print(f"   ❌ Failed to load settings: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n3. Testing PostgreSQL connection...")
try:
    from config.postgres_connection import create_connection, stop_ssh_tunnel

    # Create connection (will auto-start SSH tunnel if enabled)
    conn = create_connection(settings.postgres, settings.ssh_tunnel)
    print("   ✓ Connected to PostgreSQL")

    with conn.cursor() as cursor:
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        print(f"   PostgreSQL version: {version[:50]}...")

    conn.close()

    # Clean up SSH tunnel
    if settings.ssh_tunnel.tunnel_enabled:
        stop_ssh_tunnel()
        print("   ✓ SSH tunnel stopped")

    print("\n✓ All tests passed!")

except Exception as e:
    print(f"   ❌ Failed to connect: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
