from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from models import Admin, Merchant, PaymentSession, Student, Transaction, db
from tests.conftest import make_payload
from time_utils import utcnow_naive


def _login(client, user_id, password):
    return client.post(
        "/auth/login",
        data={"user_id": user_id, "password": password},
        follow_redirects=True,
    )


def _scan(client, seed):
    response = client.post(
        "/api/card-scan",
        json={
            "payload": make_payload(
                seed.card_uid,
                seed.card_roll,
                seed.merchant_uid,
            )
        },
    )
    assert response.status_code == 201
    return response.get_json()["session_id"]


@pytest.mark.parametrize(
    "amount",
    ["0", "-1", "0.001", "1.001", "NaN", "Infinity", "10000000000.00", "1e1000"],
)
def test_payment_rejects_invalid_precision_and_unbounded_amounts(
    client, seed, app, amount
):
    _login(client, "merchant1", "password123")
    session_id = _scan(client, seed)
    response = client.post(
        f"/merchant/enter_amount/{session_id}",
        data={"amount": amount},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        student = db.session.get(Student, seed.student_id)
        payment_session = PaymentSession.query.filter_by(session_id=session_id).first()
        assert student.balance == Decimal("100.00")
        assert payment_session.status == "awaiting_amount"
        assert Transaction.query.count() == 0


def test_merchant_reports_aggregate_only_own_successful_payments(client, seed, app):
    now = utcnow_naive()
    today_start = datetime.combine(now.date(), time.min)
    current_month_start = datetime(now.year, now.month, 1)
    if now.month == 12:
        next_month_start = datetime(now.year + 1, 1, 1)
    else:
        next_month_start = datetime(now.year, now.month + 1, 1)

    with app.app_context():
        second_merchant = Merchant(
            merchant_id="merchant2",
            name="Other Shop",
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(second_merchant)
        db.session.flush()
        admin = Admin.query.first()

        transactions = [
            Transaction(
                transaction_id="today-one",
                student_id=seed.student_id,
                merchant_id=seed.merchant_id,
                amount=Decimal("2.25"),
                transaction_type="payment",
                status="success",
                created_at=today_start + timedelta(hours=8),
            ),
            Transaction(
                transaction_id="today-two",
                student_id=seed.student_id,
                merchant_id=seed.merchant_id,
                amount=Decimal("3.75"),
                transaction_type="payment",
                status="success",
                created_at=now,
            ),
            Transaction(
                transaction_id="other-merchant",
                student_id=seed.student_id,
                merchant_id=second_merchant.id,
                amount=Decimal("20.00"),
                transaction_type="payment",
                status="success",
                created_at=now,
            ),
            Transaction(
                transaction_id="failed-payment",
                student_id=seed.student_id,
                merchant_id=seed.merchant_id,
                amount=Decimal("90.00"),
                transaction_type="payment",
                status="failed",
                created_at=now,
            ),
            Transaction(
                transaction_id="balance-addition",
                student_id=seed.student_id,
                admin_id=admin.id,
                amount=Decimal("100.00"),
                transaction_type="balance_addition",
                status="success",
                created_at=now,
            ),
            Transaction(
                transaction_id="next-month",
                student_id=seed.student_id,
                merchant_id=seed.merchant_id,
                amount=Decimal("70.00"),
                transaction_type="payment",
                status="success",
                created_at=next_month_start,
            ),
        ]
        if current_month_start < today_start:
            transactions.append(
                Transaction(
                    transaction_id="previous-month",
                    student_id=seed.student_id,
                    merchant_id=seed.merchant_id,
                    amount=Decimal("11.00"),
                    transaction_type="payment",
                    status="success",
                    created_at=current_month_start - timedelta(seconds=1),
                )
            )
        db.session.add_all(transactions)
        db.session.commit()

    _login(client, "merchant1", "password123")
    dashboard = client.get("/merchant/dashboard")
    assert dashboard.status_code == 200
    assert "₹6.00".encode() in dashboard.data
    assert b"Daily Sales" in dashboard.data
    assert b"Monthly Sales" in dashboard.data

    client.post("/auth/logout")
    _login(client, "student1", "password123")
    student_dashboard = client.get("/student/dashboard")
    assert student_dashboard.status_code == 200
    assert "₹26.00".encode() in student_dashboard.data
    assert b"today-one" in student_dashboard.data
    assert b"balance-addition" in student_dashboard.data


def test_payment_commit_failure_rolls_back_balance_transaction_and_completion(
    client, seed, app
):
    _login(client, "merchant1", "password123")
    session_id = _scan(client, seed)

    def fail_transaction_flush(session, flush_context, instances):
        if any(
            isinstance(item, Transaction) and item.transaction_type == "payment"
            for item in session.new
        ):
            raise SQLAlchemyError("simulated transaction insert failure")

    event.listen(Session, "before_flush", fail_transaction_flush)
    try:
        response = client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "10.00"},
            follow_redirects=True,
        )
    finally:
        event.remove(Session, "before_flush", fail_transaction_flush)

    assert response.status_code == 200
    with app.app_context():
        student = db.session.get(Student, seed.student_id)
        payment_session = PaymentSession.query.filter_by(session_id=session_id).first()
        assert student.balance == Decimal("100.00")
        assert payment_session.status == "failed"
        assert Transaction.query.filter_by(transaction_type="payment").count() == 0


def test_admin_credit_is_rolled_back_if_transaction_insert_fails(client, seed, app):
    _login(client, "admin1", "admin123")
    before_amount = None
    with app.app_context():
        before_amount = db.session.get(Student, seed.student_id).balance

    def fail_credit_flush(session, flush_context, instances):
        if any(
            isinstance(item, Transaction)
            and item.transaction_type == "balance_addition"
            for item in session.new
        ):
            raise SQLAlchemyError("simulated ledger insert failure")

    event.listen(Session, "before_flush", fail_credit_flush)
    try:
        response = client.post(
            "/admin/dashboard",
            data={"student_id": "student1", "amount": "25.00"},
            follow_redirects=True,
        )
    finally:
        event.remove(Session, "before_flush", fail_credit_flush)

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Student, seed.student_id).balance == before_amount
        assert Transaction.query.count() == 0


def test_admin_credit_cannot_exceed_numeric_column_capacity(client, seed, app):
    _login(client, "admin1", "admin123")
    response = client.post(
        "/admin/dashboard",
        data={"student_id": "student1", "amount": "9999999999.99"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Student, seed.student_id).balance == Decimal("100.00")
        assert Transaction.query.count() == 0
