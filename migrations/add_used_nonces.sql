-- Migration: add used_nonces table for ESP replay protection.
-- Run this against accounts.db when upgrading from a schema that
-- does not yet have the used_nonces table.
--
-- Usage:
--   sqlite3 accounts.db < migrations/add_used_nonces.sql

CREATE TABLE IF NOT EXISTS used_nonces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nonce TEXT NOT NULL UNIQUE,
    used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_used_nonces_used_at
ON used_nonces(used_at);
