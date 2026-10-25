"""Apply additive SQLite migrations after creating a database backup."""

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "accounts.db"
MIGRATION_FILES = (
    "add_used_nonces.sql",
    "add_active_payment_session_index.sql",
)


def apply_migrations(
    database_path: Path,
    backup_path: Path | None = None,
) -> Path:
    """Back up a supported application database and apply idempotent DDL."""
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")

    if backup_path is None:
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = database_path.with_name(
            f"{database_path.stem}.backup-{suffix}{database_path.suffix}"
        )
    backup_path = backup_path.resolve()
    if backup_path == database_path:
        raise ValueError("Backup path must not overwrite the source database.")
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")

    with sqlite3.connect(database_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"payment_sessions", "students", "merchants"} <= existing_tables:
            raise ValueError("The file is not a supported application database.")

        with sqlite3.connect(backup_path) as backup:
            connection.backup(backup)

        try:
            connection.execute("BEGIN IMMEDIATE")
            for migration_file in MIGRATION_FILES:
                migration_sql = (Path(__file__).parent / migration_file).read_text(
                    encoding="utf-8"
                )
                for statement in migration_sql.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database path (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Backup destination (defaults to a timestamped sibling file)",
    )
    arguments = parser.parse_args()
    backup_path = apply_migrations(arguments.database, arguments.backup)
    print(f"Migration applied successfully. Backup created at: {backup_path}")


if __name__ == "__main__":
    main()
