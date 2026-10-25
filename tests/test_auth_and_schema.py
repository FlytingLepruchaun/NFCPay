import re
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    Admin,
    Card,
    Merchant,
    Parent,
    PaymentSession,
    Student,
    Transaction,
    UsedNonce,
    db,
)


def _login(client, user_id, password):
    return client.post(
        "/auth/login",
        data={"user_id": user_id, "password": password},
        follow_redirects=True,
    )


def test_login_uses_hashed_password_and_rejects_invalid_credentials(client, seed, app):
    response = _login(client, "student1", "password123")
    assert response.status_code == 200
    assert b"John Doe" in client.get("/student/dashboard").data

    with app.app_context():
        student = Student.query.filter_by(student_id="student1").first()
        assert student.password_hash != "password123"
        assert check_password_hash(student.password_hash, "password123")

    client.post("/auth/logout")
    response = _login(client, "student1", "incorrect")
    assert response.status_code == 200
    assert b"John Doe" not in client.get("/student/dashboard").data


def test_logout_is_post_only(client, seed):
    _login(client, "student1", "password123")
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").status_code == 302


def test_role_boundaries_and_authenticated_merchant_polling(client, seed):
    assert client.get("/admin/dashboard").status_code == 302
    _login(client, "student1", "password123")
    assert client.get("/merchant/dashboard").status_code == 302
    assert client.get("/api/merchant/session-status").status_code == 403

    client.post("/auth/logout")
    _login(client, "merchant1", "password123")
    assert client.get(
        "/api/merchant/session-status?merchant_db_id=999999"
    ).get_json() == {"status": "waiting"}


def test_parent_dashboard_is_limited_to_associated_student(client, seed, app):
    with app.app_context():
        other_student = Student(
            student_id="student2",
            name="Private Student",
            password_hash=generate_password_hash("password123"),
            balance=Decimal("12.00"),
        )
        db.session.add(other_student)
        db.session.flush()
        other_parent = Parent(
            parent_id="parent2",
            student_id=other_student.id,
            name="Other Parent",
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(other_parent)
        db.session.commit()

    _login(client, "parent1", "password123")
    dashboard = client.get("/parent/dashboard")
    assert b"John Doe" in dashboard.data
    assert b"Private Student" not in dashboard.data
    assert client.get("/student/dashboard").status_code == 302
    assert client.get("/admin/dashboard").status_code == 302


def test_csrf_is_required_for_browser_posts(app, seed):
    app.config["CSRF_ENABLED"] = True
    client = app.test_client()
    try:
        login_form = client.get("/auth/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', login_form.data)
        assert match
        token = match.group(1).decode()

        assert (
            client.post(
                "/auth/login",
                data={"user_id": "student1", "password": "password123"},
            ).status_code
            == 400
        )

        assert (
            client.post(
                "/auth/login",
                data={
                    "csrf_token": token,
                    "user_id": "student1",
                    "password": "password123",
                },
            ).status_code
            == 302
        )
    finally:
        app.config["CSRF_ENABLED"] = False


def test_sqlalchemy_constraints_and_relationships(app, seed):
    with app.app_context():
        student = db.session.get(Student, seed.student_id)
        assert student is not None
        assert len(student.cards) == 1
        assert student.parent.student_id == student.id
        assert isinstance(student.balance, Decimal)
        assert db.session.get(Parent, student.parent.id) is student.parent
        assert db.session.get(Merchant, seed.merchant_id) is not None
        assert Admin.query.count() == 1
        assert UsedNonce.__tablename__ == "used_nonces"

        assert db.session.execute(db.text("PRAGMA foreign_keys")).scalar() == 1

        invalid_status = PaymentSession(
            session_id="invalid-status",
            student_id=student.id,
            merchant_id=seed.merchant_id,
            status="unknown",
            expires_at=db.func.current_timestamp(),
        )
        db.session.add(invalid_status)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        invalid_transaction = Transaction(
            transaction_id="invalid-type",
            student_id=student.id,
            amount=Decimal("1.00"),
            transaction_type="unknown",
            status="success",
        )
        db.session.add(invalid_transaction)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        duplicate_card = Card(
            student_id=student.id,
            card_uid=seed.card_uid,
            card_roll_number=seed.card_roll,
        )
        db.session.add(duplicate_card)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        orphan_card = Card(
            student_id=999999,
            card_uid="ORPHAN",
            card_roll_number="student",
        )
        db.session.add(orphan_card)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_active_session_index_allows_only_one_open_session(app, seed):
    with app.app_context():
        first = PaymentSession(
            session_id="open-one",
            student_id=seed.student_id,
            merchant_id=seed.merchant_id,
            status="awaiting_amount",
            expires_at=db.func.current_timestamp(),
        )
        db.session.add(first)
        db.session.commit()

        second = PaymentSession(
            session_id="open-two",
            student_id=seed.student_id,
            merchant_id=seed.merchant_id,
            status="processing",
            expires_at=db.func.current_timestamp(),
        )
        db.session.add(second)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_app_refuses_predictable_or_missing_secrets():
    from app import create_app

    class UnsafeConfig:
        SECRET_KEY = "predictable"
        CARD_SCAN_KEY = None

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(UnsafeConfig)


def test_card_scan_key_requires_exact_64_hex_characters():
    from app import _validate_security_configuration

    valid_config = {"SECRET_KEY": "s" * 32, "CARD_SCAN_KEY": "AB" * 32}
    _validate_security_configuration(valid_config)

    invalid_keys = (
        None,
        "",
        "a" * 63,
        "a" * 65,
        "g" + "a" * 63,
        " " + "a" * 63,
        "a" * 63 + "\n",
    )
    for key in invalid_keys:
        with pytest.raises(RuntimeError, match="CARD_SCAN_KEY"):
            _validate_security_configuration(
                {"SECRET_KEY": "s" * 32, "CARD_SCAN_KEY": key}
            )
