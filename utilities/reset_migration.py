#!/usr/bin/env python3
"""
Reset migration state for fresh start.

Clears staging files, checkpoints, and optionally logs.
Useful during testing or to restart migration from scratch.

Usage:
    python utilities/reset_migration.py          # Clear staging and checkpoints
    python utilities/reset_migration.py --all    # Also clear logs
    python utilities/reset_migration.py --dry-run  # Show what would be deleted
"""

import argparse
import shutil
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_directories() -> dict[str, Path]:
    """Get migration directories from settings or defaults."""
    try:
        from config.settings import get_settings
        settings = get_settings()
        return {
            "staging": Path(settings.migration.staging_dir),
            "checkpoints": Path(settings.migration.checkpoint_dir),
            "logs": Path(settings.migration.log_dir),
        }
    except Exception:
        # Fallback to defaults if settings can't be loaded
        return {
            "staging": PROJECT_ROOT / "data" / "staging",
            "checkpoints": PROJECT_ROOT / "data" / "checkpoints",
            "logs": PROJECT_ROOT / "logs",
        }


def count_files(directory: Path, pattern: str = "*") -> tuple[int, int]:
    """
    Count files and total size in directory.

    Returns:
        tuple: (file_count, total_bytes)
    """
    if not directory.exists():
        return 0, 0

    total_files = 0
    total_bytes = 0

    for file_path in directory.rglob(pattern):
        if file_path.is_file():
            total_files += 1
            total_bytes += file_path.stat().st_size

    return total_files, total_bytes


def format_size(bytes_count: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_count < 1024:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024
    return f"{bytes_count:.1f} TB"


def clear_directory(directory: Path, pattern: str = "*", dry_run: bool = False) -> int:
    """
    Clear files from directory.

    Args:
        directory: Directory to clear
        pattern: Glob pattern for files to delete
        dry_run: If True, only report what would be deleted

    Returns:
        int: Number of files deleted
    """
    if not directory.exists():
        return 0

    deleted = 0

    for file_path in directory.rglob(pattern):
        if file_path.is_file():
            if dry_run:
                print(f"  Would delete: {file_path}")
            else:
                file_path.unlink()
            deleted += 1

    # Also remove empty subdirectories
    if not dry_run:
        for subdir in sorted(directory.rglob("*"), reverse=True):
            if subdir.is_dir() and not any(subdir.iterdir()):
                subdir.rmdir()

    return deleted


def main():
    parser = argparse.ArgumentParser(
        description="Reset migration state for fresh start",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python utilities/reset_migration.py              Clear staging and checkpoints
  python utilities/reset_migration.py --all        Also clear logs
  python utilities/reset_migration.py --dry-run    Preview what would be deleted
  python utilities/reset_migration.py --staging    Only clear staging files
  python utilities/reset_migration.py --checkpoints  Only clear checkpoints
        """,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also clear log files",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Only clear staging directory",
    )
    parser.add_argument(
        "--checkpoints",
        action="store_true",
        help="Only clear checkpoints directory",
    )
    parser.add_argument(
        "--logs",
        action="store_true",
        help="Only clear logs directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    # Determine which directories to clear
    dirs = get_directories()

    # If specific flags given, only clear those
    if args.staging or args.checkpoints or args.logs:
        to_clear = {}
        if args.staging:
            to_clear["staging"] = dirs["staging"]
        if args.checkpoints:
            to_clear["checkpoints"] = dirs["checkpoints"]
        if args.logs:
            to_clear["logs"] = dirs["logs"]
    else:
        # Default: staging and checkpoints (not logs unless --all)
        to_clear = {
            "staging": dirs["staging"],
            "checkpoints": dirs["checkpoints"],
        }
        if args.all:
            to_clear["logs"] = dirs["logs"]

    # Show summary
    print("\n" + "=" * 60)
    print("MIGRATION RESET UTILITY")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN - No files will be deleted]\n")

    total_files = 0
    total_size = 0

    for name, directory in to_clear.items():
        files, size = count_files(directory)
        total_files += files
        total_size += size

        status = "EXISTS" if directory.exists() else "NOT FOUND"
        print(f"\n{name.upper()}: {directory}")
        print(f"  Status: {status}")
        print(f"  Files: {files}")
        print(f"  Size: {format_size(size)}")

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_files} files, {format_size(total_size)}")
    print("=" * 60)

    if total_files == 0:
        print("\nNothing to clear.")
        return 0

    # Confirm unless -y or --dry-run
    if not args.yes and not args.dry_run:
        response = input("\nProceed with deletion? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted.")
            return 1

    # Perform deletion
    print()
    for name, directory in to_clear.items():
        if not directory.exists():
            continue

        print(f"Clearing {name}...", end=" ")

        if name == "staging":
            # For staging, clear all files including subdirectories
            deleted = clear_directory(directory, "*", args.dry_run)
        elif name == "checkpoints":
            # For checkpoints, only clear JSON files
            deleted = clear_directory(directory, "*.json", args.dry_run)
        elif name == "logs":
            # For logs, only clear log files
            deleted = clear_directory(directory, "*.log", args.dry_run)
        else:
            deleted = clear_directory(directory, "*", args.dry_run)

        if args.dry_run:
            print(f"would delete {deleted} files")
        else:
            print(f"deleted {deleted} files")

    if args.dry_run:
        print("\n[DRY RUN COMPLETE - No files were deleted]")
    else:
        print("\nReset complete. Ready for fresh migration.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
