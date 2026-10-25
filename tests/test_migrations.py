import sqlite3

from migrations.run_migration import apply_migrations


def test_additive_migration_backups_and_preserves_existing_rows(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE students (id INTEGER PRIMARY KEY);
            CREATE TABLE merchants (id INTEGER PRIMARY KEY);
            CREATE TABLE payment_sessions (
                id INTEGER PRIMARY KEY,
                merchant_id INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO students (id) VALUES (1);
            INSERT INTO merchants (id) VALUES (1);
            INSERT INTO payment_sessions (id, merchant_id, status)
            VALUES (1, 1, 'completed');
            """
        )

    backup_path = tmp_path / "legacy-backup.db"
    assert apply_migrations(database_path, backup_path) == backup_path
    assert backup_path.exists()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT status FROM payment_sessions WHERE id = 1"
            ).fetchone()[0]
            == "completed"
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "used_nonces" in table_names
        assert "uq_payment_sessions_active_merchant" in index_names

    with sqlite3.connect(backup_path) as backup:
        table_names = {
            row[0]
            for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "used_nonces" not in table_names
