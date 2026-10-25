PRAGMA foreign_keys = ON;

-- 1. Students
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    balance NUMERIC NOT NULL DEFAULT 0
);


-- 2. Parents
CREATE TABLE parents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,

    FOREIGN KEY (student_id)
        REFERENCES students(id)
);


-- 3. Merchants
CREATE TABLE merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL
);


-- 4. Admins
CREATE TABLE admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL
);


-- 5. Cards
CREATE TABLE cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    card_uid TEXT NOT NULL UNIQUE,
    card_roll_number TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (student_id)
        REFERENCES students(id)
);


-- 6. Transactions
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL,
    merchant_id INTEGER,
    admin_id INTEGER,
    amount NUMERIC NOT NULL,
    transaction_type TEXT NOT NULL
        CHECK (transaction_type IN ('payment', 'balance_addition')),
    status TEXT NOT NULL
        CHECK (status IN ('success', 'failed')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (student_id)
        REFERENCES students(id),

    FOREIGN KEY (merchant_id)
        REFERENCES merchants(id),

    FOREIGN KEY (admin_id)
        REFERENCES admins(id)
);


-- 7. Payment Sessions
CREATE TABLE payment_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL,
    merchant_id INTEGER NOT NULL,
    amount NUMERIC,
    status TEXT NOT NULL
        CHECK (
            status IN (
                'awaiting_amount',
                'processing',
                'completed',
                'failed',
                'expired',
                'cancelled'
            )
        ),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    completed_at DATETIME,

    FOREIGN KEY (student_id)
        REFERENCES students(id),

    FOREIGN KEY (merchant_id)
        REFERENCES merchants(id)
);

CREATE INDEX idx_cards_student_id
ON cards(student_id);

CREATE INDEX idx_transactions_student_id
ON transactions(student_id);

CREATE INDEX idx_transactions_merchant_id
ON transactions(merchant_id);

CREATE INDEX idx_transactions_admin_id
ON transactions(admin_id);

CREATE INDEX idx_transactions_created_at
ON transactions(created_at);

CREATE INDEX idx_payment_sessions_student_id
ON payment_sessions(student_id);

CREATE INDEX idx_payment_sessions_merchant_id
ON payment_sessions(merchant_id);

CREATE INDEX idx_payment_sessions_status
ON payment_sessions(status);

CREATE UNIQUE INDEX uq_payment_sessions_active_merchant
ON payment_sessions(merchant_id)
WHERE status IN ('awaiting_amount', 'processing');


-- 8. Used Nonces (replay protection for ESP card-scan payloads)
CREATE TABLE used_nonces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nonce TEXT NOT NULL UNIQUE,          -- hex-encoded 12-byte AES-GCM IV
    used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_used_nonces_used_at
ON used_nonces(used_at);
