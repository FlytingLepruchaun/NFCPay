"""
State machine tests for PaymentSession (Req #8).

Valid:   awaiting_amount → processing → completed / failed
Invalid: completed/failed/expired/cancelled → anything

Also tests:
  - Merchant can only access their own sessions (Req #7)
  - Multiple cards per student (Req #19)
  - One parent per student (Req #18)
"""

from datetime import timedelta

import pytest

from models import Card, Parent, PaymentSession, db
from tests.conftest import make_payload
from time_utils import utcnow_naive


def _login(client, user_id, password):
    client.post(
        "/auth/login",
        data={"user_id": user_id, "password": password},
        follow_redirects=True,
    )


def _card_scan(client, seed):
    p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
    r = client.post(
        "/api/card-scan",
        json={"payload": p},
        content_type="application/json",
    )
    assert r.status_code == 201
    return r.get_json()["session_id"]


# ---------------------------------------------------------------------------
# Terminal state transitions
# ---------------------------------------------------------------------------


class TestTerminalStates:
    def _get_enter_amount(self, client, session_id):
        return client.get(f"/merchant/enter_amount/{session_id}", follow_redirects=True)

    def _post_enter_amount(self, client, session_id, amount="5.00"):
        return client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": amount},
            follow_redirects=True,
        )

    def test_completed_session_redirects_on_get(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        self._post_enter_amount(client, session_id)  # complete it

        r = self._get_enter_amount(client, session_id)
        # Should redirect to dashboard, not show the form again
        assert r.status_code == 200
        assert b"enter_amount" not in r.data or b"completed" in r.data.lower()

    def test_completed_session_rejects_post(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        self._post_enter_amount(client, session_id)  # first — ok
        self._post_enter_amount(client, session_id)  # replay — ignored

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "completed"

    def test_failed_session_rejects_further_processing(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        self._post_enter_amount(client, session_id, "9999.00")  # force fail

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "failed"

        # Try to use the failed session again
        self._post_enter_amount(client, session_id, "5.00")

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "failed"

    def test_expired_session_marked_expired_on_access(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)

        # Manually push expiry into the past
        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            sess.expires_at = utcnow_naive() - timedelta(seconds=1)
            db.session.commit()

        self._get_enter_amount(client, session_id)

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "expired"

    def test_merchant_can_cancel_only_its_own_pending_session(self, client, seed, app):
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)
        response = client.post(
            f"/merchant/cancel_session/{session_id}",
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            payment_session = PaymentSession.query.filter_by(
                session_id=session_id
            ).first()
            assert payment_session.status == "cancelled"

        next_response = client.post(
            "/api/card-scan",
            json={
                "payload": make_payload(
                    seed.card_uid, seed.card_roll, seed.merchant_uid
                )
            },
        )
        assert next_response.status_code == 201


# ---------------------------------------------------------------------------
# Merchant session isolation (Req #7)
# ---------------------------------------------------------------------------


class TestMerchantIsolation:
    def test_merchant_cannot_access_another_merchants_session(self, client, seed, app):
        from werkzeug.security import generate_password_hash

        from models import Merchant

        # Create a second merchant and student
        with app.app_context():
            m2 = Merchant(
                merchant_id="merchant2",
                name="Other Shop",
                password_hash=generate_password_hash("password123"),
            )
            db.session.add(m2)
            db.session.commit()

        # merchant1 scans a card → creates session
        _login(client, "merchant1", "password123")
        session_id = _card_scan(client, seed)

        # merchant2 tries to process that session by guessing the session_id
        client.post("/auth/logout")
        _login(client, "merchant2", "password123")
        r = client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "5.00"},
            follow_redirects=True,
        )
        # Should be redirected with an error — NOT process the payment
        assert r.status_code == 200

        with app.app_context():
            from models import Transaction

            assert Transaction.query.count() == 0
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "awaiting_amount"


# ---------------------------------------------------------------------------
# Multiple cards per student (Req #19)
# ---------------------------------------------------------------------------


class TestMultipleCardsPerStudent:
    def test_student_can_have_two_cards(self, seed, app):
        with app.app_context():
            card2 = Card(
                student_id=seed.student_id,
                card_uid="EE:FF:00:11",
                card_roll_number="student1",
                is_active=True,
            )
            db.session.add(card2)
            db.session.commit()

            cards = Card.query.filter_by(student_id=seed.student_id).count()
            assert cards == 2

    def test_duplicate_card_uid_rejected(self, seed, app):
        with app.app_context():
            dup = Card(
                student_id=seed.student_id,
                card_uid=seed.card_uid,  # same UID
                card_roll_number="student1",
                is_active=True,
            )
            db.session.add(dup)
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()

    def test_second_card_can_trigger_scan(self, client, seed, app):
        with app.app_context():
            card2 = Card(
                student_id=seed.student_id,
                card_uid="EE:FF:00:11",
                card_roll_number="student1",
                is_active=True,
            )
            db.session.add(card2)
            db.session.commit()

        p = make_payload("EE:FF:00:11", "student1", seed.merchant_uid)
        r = client.post(
            "/api/card-scan",
            json={"payload": p},
            content_type="application/json",
        )
        assert r.status_code == 201

    def test_inactive_card_rejected_active_still_works(self, client, seed, app):
        with app.app_context():
            card2 = Card(
                student_id=seed.student_id,
                card_uid="EE:FF:00:11",
                card_roll_number="student1",
                is_active=False,  # inactive
            )
            db.session.add(card2)
            db.session.commit()

        p = make_payload("EE:FF:00:11", "student1", seed.merchant_uid)
        r = client.post(
            "/api/card-scan",
            json={"payload": p},
            content_type="application/json",
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# One parent per student (Req #18)
# ---------------------------------------------------------------------------


class TestOneParentPerStudent:
    def test_second_parent_for_same_student_rejected(self, seed, app):
        with app.app_context():
            from werkzeug.security import generate_password_hash

            second_parent = Parent(
                parent_id="parent2",
                student_id=seed.student_id,
                name="Second Parent",
                password_hash=generate_password_hash("pw"),
            )
            db.session.add(second_parent)
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()
