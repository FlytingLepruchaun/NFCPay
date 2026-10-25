"""
Payment flow tests (Req #8, #13, #14, #15, #16, #17, #23).

Covers:
  - Atomic payment (balance + transaction + session in one commit)
  - Decimal money handling
  - Duplicate payment protection
  - Failed payment semantics
  - Merchant sales filters
  - Admin balance addition atomicity
"""

from decimal import Decimal

import pytest

from models import PaymentSession, Student, Transaction, db
from tests.conftest import make_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client, user_id, password):
    return client.post(
        "/auth/login",
        data={"user_id": user_id, "password": password},
        follow_redirects=True,
    )


def _card_scan(client, seed):
    """Simulate a card scan and return the session_id."""
    p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
    r = client.post(
        "/api/card-scan",
        json={"payload": p},
        content_type="application/json",
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()["session_id"]


def _enter_amount(client, session_id, amount):
    return client.post(
        f"/merchant/enter_amount/{session_id}",
        data={"amount": str(amount)},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Atomic payment (Req #14)
# ---------------------------------------------------------------------------


class TestAtomicPayment:
    def test_successful_payment_deducts_balance(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)

        with app.app_context():
            before = Student.query.filter_by(student_id="student1").first().balance

        _enter_amount(client, session_id, "10.00")

        with app.app_context():
            after = Student.query.filter_by(student_id="student1").first().balance
            assert after == before - Decimal("10.00")

    def test_successful_payment_creates_transaction(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "5.50")

        with app.app_context():
            tx = Transaction.query.filter_by(
                transaction_type="payment", status="success"
            ).first()
            assert tx is not None
            assert tx.amount == Decimal("5.50")

    def test_successful_payment_marks_session_completed(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "5.00")

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "completed"
            assert sess.completed_at is not None


# ---------------------------------------------------------------------------
# Insufficient balance (Req #15)
# ---------------------------------------------------------------------------


class TestInsufficientBalance:
    def test_insufficient_balance_rejected(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "999.99")  # student has 100

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "failed"

    def test_balance_unchanged_on_insufficient_funds(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)

        with app.app_context():
            before = Student.query.filter_by(student_id="student1").first().balance

        _enter_amount(client, session_id, "999.99")

        with app.app_context():
            after = Student.query.filter_by(student_id="student1").first().balance
            assert after == before

    def test_no_transaction_on_insufficient_funds(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "999.99")

        with app.app_context():
            assert Transaction.query.count() == 0


# ---------------------------------------------------------------------------
# Duplicate payment protection (Req #13)
# ---------------------------------------------------------------------------


class TestDuplicatePaymentProtection:
    def test_completed_session_cannot_be_reprocessed(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "5.00")  # first — succeeds
        _enter_amount(client, session_id, "5.00")  # replay — must be ignored

        with app.app_context():
            # Balance should only be deducted once
            balance = Student.query.filter_by(student_id="student1").first().balance
            assert balance == Decimal("95.00")
            assert Transaction.query.count() == 1

    def test_completed_session_status_stays_completed(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "5.00")
        _enter_amount(client, session_id, "5.00")

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "completed"


# ---------------------------------------------------------------------------
# Decimal money (Req #23)
# ---------------------------------------------------------------------------


class TestDecimalMoney:
    @pytest.mark.parametrize("amount", ["0.01", "1.10", "10.25", "99.99"])
    def test_payment_stores_correct_decimal(self, client, seed, app, amount):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, amount)

        with app.app_context():
            tx = Transaction.query.filter_by(
                transaction_type="payment", status="success"
            ).first()
            assert tx is not None
            assert tx.amount == Decimal(amount)

    def test_balance_stored_as_numeric_not_float(self, client, seed, app):
        with app.app_context():
            student = Student.query.filter_by(student_id="student1").first()
            assert isinstance(student.balance, Decimal)


# ---------------------------------------------------------------------------
# Merchant sales filter (Req #16)
# ---------------------------------------------------------------------------


class TestMerchantSalesFilter:
    def test_merchant_sees_own_transactions_only(self, client, seed, app):
        from models import Merchant

        # Add a second merchant and student
        with app.app_context():
            m2 = Merchant(
                merchant_id="merchant2",
                name="Second Shop",
                password_hash="x",
            )
            db.session.add(m2)
            db.session.commit()

        # Pay via merchant1
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        _enter_amount(client, session_id, "10.00")

        with app.app_context():
            merchant1 = Merchant.query.filter_by(merchant_id="merchant1").first()
            merchant2 = Merchant.query.filter_by(merchant_id="merchant2").first()
            m1_txns = Transaction.query.filter_by(
                merchant_id=merchant1.id,
                transaction_type="payment",
                status="success",
            ).count()
            m2_txns = Transaction.query.filter_by(
                merchant_id=merchant2.id,
                transaction_type="payment",
                status="success",
            ).count()
            assert m1_txns == 1
            assert m2_txns == 0

    def test_balance_additions_excluded_from_sales(self, client, seed, app):
        # Admin adds balance
        _login(client, "admin1", "admin123")
        client.post(
            "/admin/dashboard",
            data={"student_id": "student1", "amount": "50.00"},
            follow_redirects=True,
        )

        with app.app_context():
            from models import Merchant

            merchant = Merchant.query.filter_by(merchant_id="merchant1").first()
            sales = Transaction.query.filter_by(
                merchant_id=merchant.id,
                transaction_type="payment",
                status="success",
            ).count()
            assert sales == 0


# ---------------------------------------------------------------------------
# Admin balance addition (Req #17)
# ---------------------------------------------------------------------------


class TestAdminBalanceAddition:
    def test_balance_increases_atomically(self, client, seed, app):
        _login(client, "admin1", "admin123")

        with app.app_context():
            before = Student.query.filter_by(student_id="student1").first().balance

        client.post(
            "/admin/dashboard",
            data={"student_id": "student1", "amount": "25.00"},
            follow_redirects=True,
        )

        with app.app_context():
            after = Student.query.filter_by(student_id="student1").first().balance
            assert after == before + Decimal("25.00")

    def test_balance_addition_transaction_created(self, client, seed, app):
        _login(client, "admin1", "admin123")
        client.post(
            "/admin/dashboard",
            data={"student_id": "student1", "amount": "25.00"},
            follow_redirects=True,
        )

        with app.app_context():
            tx = Transaction.query.filter_by(
                transaction_type="balance_addition", status="success"
            ).first()
            assert tx is not None
            assert tx.amount == Decimal("25.00")
            assert tx.admin_id == seed.admin_id

    def test_invalid_student_rejected(self, client, seed, app):
        _login(client, "admin1", "admin123")
        r = client.post(
            "/admin/dashboard",
            data={"student_id": "nonexistent", "amount": "10.00"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        with app.app_context():
            assert Transaction.query.count() == 0

    def test_invalid_amount_rejected(self, client, seed, app):
        _login(client, "admin1", "admin123")
        client.post(
            "/admin/dashboard",
            data={"student_id": "student1", "amount": "-5"},
            follow_redirects=True,
        )
        with app.app_context():
            assert Transaction.query.count() == 0
