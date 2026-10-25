"""
End-to-end test (Req #25).

Simulates the full architecture path:

  Test ESP (encrypt) → HTTP → Flask (decrypt/auth) → card validation
  → PaymentSession → Merchant enters amount → Atomic payment
  → Transaction created → Dashboard/history reflects result.
"""

from decimal import Decimal

from models import PaymentSession, Student, Transaction
from tests.conftest import make_payload


def _login(client, user_id, password):
    client.post(
        "/auth/login",
        data={"user_id": user_id, "password": password},
        follow_redirects=True,
    )


def test_merchant_scan_page_loads_polling_asset(client, seed):
    _login(client, "merchant1", "password123")

    page = client.get("/merchant/scan")
    assert page.status_code == 200
    assert b"/static/merchant_scan.js" in page.data

    script = client.get("/static/merchant_scan.js")
    assert script.status_code == 200
    assert b"/api/merchant/session-status" in script.data


# ---------------------------------------------------------------------------
# Full successful payment flow
# ---------------------------------------------------------------------------


class TestEndToEndSuccessfulPayment:
    def test_full_payment_flow(self, client, seed, app):
        # Step 1: Merchant logs in
        _login(client, "merchant1", "password123")

        # Step 2: Simulated ESP encrypts payload and sends to Flask
        payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r = client.post(
            "/api/card-scan",
            json={"payload": payload},
            content_type="application/json",
        )
        assert r.status_code == 201
        session_id = r.get_json()["session_id"]
        assert r.get_json()["student_name"] == "John Doe"

        # Step 3: Merchant browser polls and sees the session
        r_poll = client.get("/api/merchant/session-status")
        assert r_poll.status_code == 200
        poll_data = r_poll.get_json()
        assert poll_data["status"] == "awaiting_amount"
        assert poll_data["session_id"] == session_id

        # Step 4: Merchant enters amount
        with app.app_context():
            before_balance = (
                Student.query.filter_by(student_id="student1").first().balance
            )

        r_pay = client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "15.75"},
            follow_redirects=True,
        )
        assert r_pay.status_code == 200

        # Step 5: Verify database state
        with app.app_context():
            # Balance correctly deducted
            after_balance = (
                Student.query.filter_by(student_id="student1").first().balance
            )
            assert after_balance == before_balance - Decimal("15.75")

            # Session completed
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "completed"
            assert sess.amount == Decimal("15.75")
            assert sess.completed_at is not None

            # Transaction recorded correctly
            tx = Transaction.query.filter_by(
                transaction_type="payment", status="success"
            ).first()
            assert tx is not None
            assert tx.amount == Decimal("15.75")
            assert tx.student_id == seed.student_id
            assert tx.merchant_id == seed.merchant_id

        # Step 6: Polling now returns 'waiting' (session consumed)
        r_poll2 = client.get("/api/merchant/session-status")
        assert r_poll2.get_json()["status"] == "waiting"

        # Step 7: Student dashboard reflects the transaction
        client.post("/auth/logout")
        _login(client, "student1", "password123")
        r_dash = client.get("/student/dashboard")
        assert r_dash.status_code == 200


# ---------------------------------------------------------------------------
# Full failed payment flow
# ---------------------------------------------------------------------------


class TestEndToEndFailedPayment:
    def test_insufficient_balance_flow(self, client, seed, app):
        _login(client, "merchant1", "password123")

        payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r = client.post(
            "/api/card-scan",
            json={"payload": payload},
            content_type="application/json",
        )
        assert r.status_code == 201
        session_id = r.get_json()["session_id"]

        with app.app_context():
            before = Student.query.filter_by(student_id="student1").first().balance

        # Amount exceeds balance
        client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "9999.00"},
            follow_redirects=True,
        )

        with app.app_context():
            # Balance unchanged
            after = Student.query.filter_by(student_id="student1").first().balance
            assert after == before

            # Session is failed
            sess = PaymentSession.query.filter_by(session_id=session_id).first()
            assert sess.status == "failed"

            # No transaction created
            assert Transaction.query.count() == 0


# ---------------------------------------------------------------------------
# Duplicate payment protection end-to-end
# ---------------------------------------------------------------------------


class TestEndToEndDuplicateProtection:
    def test_double_submit_charges_only_once(self, client, seed, app):
        _login(client, "merchant1", "password123")
        payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r = client.post(
            "/api/card-scan",
            json={"payload": payload},
            content_type="application/json",
        )
        session_id = r.get_json()["session_id"]

        # Submit the payment form twice (simulating a double-click)
        client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "10.00"},
            follow_redirects=True,
        )
        client.post(
            f"/merchant/enter_amount/{session_id}",
            data={"amount": "10.00"},
            follow_redirects=True,
        )

        with app.app_context():
            balance = Student.query.filter_by(student_id="student1").first().balance
            assert balance == Decimal("90.00")  # deducted only once
            assert (
                Transaction.query.filter_by(
                    transaction_type="payment", status="success"
                ).count()
                == 1
            )


# ---------------------------------------------------------------------------
# New scan after completed session (Req #10)
# ---------------------------------------------------------------------------


class TestNewScanAfterCompletion:
    def test_new_scan_possible_after_completed_session(self, client, seed, app):
        _login(client, "merchant1", "password123")

        # First payment
        p1 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r1 = client.post(
            "/api/card-scan",
            json={"payload": p1},
            content_type="application/json",
        )
        session_id_1 = r1.get_json()["session_id"]
        client.post(
            f"/merchant/enter_amount/{session_id_1}",
            data={"amount": "5.00"},
            follow_redirects=True,
        )

        # Second scan — previous session is completed so this should succeed
        p2 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r2 = client.post(
            "/api/card-scan",
            json={"payload": p2},
            content_type="application/json",
        )
        assert r2.status_code == 201
        assert r2.get_json()["session_id"] != session_id_1
