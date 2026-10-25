"""
Shared pytest fixtures for the Campus Payment System test suite.

IMPORTANT: Tests NEVER touch accounts.db. All tests use an isolated
in-memory SQLite database created fresh for each test session.
"""

import base64
import json
import os
import secrets
import time
from typing import NamedTuple, Optional

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from werkzeug.security import generate_password_hash

# Point at a test-specific key so tests are independent of .env
TEST_KEY_HEX = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"

os.environ.setdefault("CARD_SCAN_KEY", TEST_KEY_HEX)

from app import create_app  # noqa: E402  (must come after env setup)
from config import Config  # noqa: E402
from models import (  # noqa: E402
    Admin,
    Card,
    Merchant,
    Parent,
    Student,
    db,
)

# ---------------------------------------------------------------------------
# Test config
# ---------------------------------------------------------------------------


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CSRF_ENABLED = False
    SECRET_KEY = "test-secret-key-for-isolated-tests"
    CARD_SCAN_KEY = TEST_KEY_HEX
    REPLAY_WINDOW_SECONDS = 30
    SESSION_COOKIE_SECURE = False


class SeedData(NamedTuple):
    student_id: int
    card_uid: str
    card_roll: str
    merchant_id: int
    merchant_uid: str
    admin_id: int


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    """Create a Flask test application with an isolated in-memory DB."""
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()  # safe — in-memory test DB only
        yield application


@pytest.fixture(autouse=True)
def clean_db(app):
    """Wipe all rows between tests to ensure isolation."""
    with app.app_context():
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()
    yield


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def seed(app) -> SeedData:
    """
    Seed the test DB with one student, parent, merchant, admin, and card.
    Return the identifying values used by the tests.
    """
    with app.app_context():
        student = Student(
            student_id="student1",
            name="John Doe",
            password_hash=generate_password_hash("password123"),
            balance=100,
        )
        db.session.add(student)
        db.session.flush()

        parent = Parent(
            parent_id="parent1",
            student_id=student.id,
            name="Jane Doe",
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(parent)

        merchant = Merchant(
            merchant_id="merchant1",
            name="Campus Cafeteria",
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(merchant)

        admin = Admin(
            admin_id="admin1",
            name="System Admin",
            password_hash=generate_password_hash("admin123"),
        )
        db.session.add(admin)

        card = Card(
            student_id=student.id,
            card_uid="AA:BB:CC:DD",
            card_roll_number="student1",
            is_active=True,
        )
        db.session.add(card)
        db.session.commit()

        return SeedData(
            student_id=student.id,
            card_uid="AA:BB:CC:DD",
            card_roll="student1",
            merchant_id=merchant.id,
            merchant_uid="merchant1",
            admin_id=admin.id,
        )


# ---------------------------------------------------------------------------
# Encryption helpers (mirror of esp_simulator.py)
# ---------------------------------------------------------------------------


def make_payload(
    card_uid: str,
    card_roll_number: str,
    merchant_id: str,
    key_hex: str = TEST_KEY_HEX,
    ts: Optional[int] = None,
    iv: Optional[bytes] = None,
) -> str:
    """
    Build a valid AES-256-GCM encrypted payload as the ESP would.
    Optionally supply a custom timestamp or IV for negative tests.
    """
    if ts is None:
        ts = int(time.time())
    if iv is None:
        iv = secrets.token_bytes(12)

    plaintext = json.dumps(
        {
            "card_uid": card_uid,
            "card_roll_number": card_roll_number,
            "merchant_id": merchant_id,
            "ts": ts,
        }
    ).encode("utf-8")

    key = bytes.fromhex(key_hex)
    aesgcm = AESGCM(key)
    ct_and_tag = aesgcm.encrypt(iv, plaintext, None)
    blob = iv + ct_and_tag
    return base64.b64encode(blob).decode("ascii")
