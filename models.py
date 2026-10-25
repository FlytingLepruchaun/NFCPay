"""SQLAlchemy models for the campus payment application."""

import sqlite3
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Engine, Index, event, text

from time_utils import utcnow_naive

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Enable SQLite foreign-key enforcement on each new database connection."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0"),
    )

    parent = db.relationship("Parent", back_populates="student", uselist=False)

    cards = db.relationship(
        "Card", back_populates="student", cascade="all, delete-orphan"
    )

    transactions = db.relationship("Transaction", back_populates="student")

    payment_sessions = db.relationship("PaymentSession", back_populates="student")

    def __repr__(self):
        return f"<Student {self.student_id}>"


class Parent(db.Model):
    __tablename__ = "parents"

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.String(100), unique=True, nullable=False)
    student_id = db.Column(
        db.Integer, db.ForeignKey("students.id"), unique=True, nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    student = db.relationship("Student", back_populates="parent")

    def __repr__(self):
        return f"<Parent {self.parent_id}>"


class Merchant(db.Model):
    __tablename__ = "merchants"

    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    transactions = db.relationship("Transaction", back_populates="merchant")

    payment_sessions = db.relationship("PaymentSession", back_populates="merchant")

    def __repr__(self):
        return f"<Merchant {self.merchant_id}>"


class Admin(db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    transactions = db.relationship("Transaction", back_populates="admin")

    def __repr__(self):
        return f"<Admin {self.admin_id}>"


class Card(db.Model):
    __tablename__ = "cards"
    __table_args__ = (Index("idx_cards_student_id", "student_id"),)

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    card_uid = db.Column(db.String(100), unique=True, nullable=False)
    card_roll_number = db.Column(db.String(100), nullable=False)
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=text("1")
    )

    student = db.relationship("Student", back_populates="cards")

    def __repr__(self):
        return f"<Card {self.card_uid}>"


class Transaction(db.Model):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('payment', 'balance_addition')",
            name="ck_transactions_type",
        ),
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_transactions_status",
        ),
        Index("idx_transactions_student_id", "student_id"),
        Index("idx_transactions_merchant_id", "merchant_id"),
        Index("idx_transactions_admin_id", "admin_id"),
        Index("idx_transactions_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(100), unique=True, nullable=False)

    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)

    merchant_id = db.Column(db.Integer, db.ForeignKey("merchants.id"), nullable=True)

    admin_id = db.Column(db.Integer, db.ForeignKey("admins.id"), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)

    transaction_type = db.Column(db.String(30), nullable=False)

    status = db.Column(db.String(20), nullable=False)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    student = db.relationship("Student", back_populates="transactions")

    merchant = db.relationship("Merchant", back_populates="transactions")

    admin = db.relationship("Admin", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction {self.transaction_id}>"


class PaymentSession(db.Model):
    __tablename__ = "payment_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('awaiting_amount', 'processing', 'completed', 'failed', "
            "'expired', 'cancelled')",
            name="ck_payment_sessions_status",
        ),
        Index("idx_payment_sessions_student_id", "student_id"),
        Index("idx_payment_sessions_merchant_id", "merchant_id"),
        Index("idx_payment_sessions_status", "status"),
        Index(
            "uq_payment_sessions_active_merchant",
            "merchant_id",
            unique=True,
            sqlite_where=text("status IN ('awaiting_amount', 'processing')"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    session_id = db.Column(db.String(100), unique=True, nullable=False)

    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)

    merchant_id = db.Column(db.Integer, db.ForeignKey("merchants.id"), nullable=False)

    amount = db.Column(db.Numeric(12, 2), nullable=True)

    status = db.Column(db.String(30), nullable=False)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    expires_at = db.Column(db.DateTime, nullable=False)

    completed_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship("Student", back_populates="payment_sessions")

    merchant = db.relationship("Merchant", back_populates="payment_sessions")

    def __repr__(self):
        return f"<PaymentSession {self.session_id}>"


class UsedNonce(db.Model):
    """Tracks AES-GCM IVs already consumed to prevent replay attacks."""

    __tablename__ = "used_nonces"
    __table_args__ = (Index("idx_used_nonces_used_at", "used_at"),)

    id = db.Column(db.Integer, primary_key=True)
    # Hex-encoded 12-byte AES-GCM IV.
    nonce = db.Column(db.String(24), unique=True, nullable=False)
    used_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self):
        return f"<UsedNonce {self.nonce}>"
