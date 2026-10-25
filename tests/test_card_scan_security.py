"""
Tests for card-scan endpoint security (Req #24).

Every failure path must NOT create a payment session.
"""

import base64
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from models import PaymentSession, db
from tests.conftest import TEST_KEY_HEX, make_payload
from time_utils import utcnow_naive

URL = "/api/card-scan"


def encrypt_plaintext(plaintext, key_hex=TEST_KEY_HEX, iv=None):
    iv = iv or secrets.token_bytes(12)
    encrypted = AESGCM(bytes.fromhex(key_hex)).encrypt(iv, plaintext, None)
    return base64.b64encode(iv + encrypted).decode("ascii")


def post(client, payload_b64):
    return client.post(
        URL, json={"payload": payload_b64}, content_type="application/json"
    )


# ---------------------------------------------------------------------------
# Valid request
# ---------------------------------------------------------------------------


class TestValidRequest:
    def test_valid_encrypted_payload_creates_session(self, client, seed, app):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r = post(client, p)
        assert r.status_code == 201
        data = r.get_json()
        assert "session_id" in data
        assert data["student_name"] == "John Doe"

        with app.app_context():
            sess = PaymentSession.query.filter_by(session_id=data["session_id"]).first()
            assert sess is not None
            assert sess.status == "awaiting_amount"


# ---------------------------------------------------------------------------
# Missing / malformed payload
# ---------------------------------------------------------------------------


class TestMalformedPayload:
    def test_missing_payload_field(self, client, seed):
        r = client.post(URL, json={}, content_type="application/json")
        assert r.status_code == 400

    def test_additional_top_level_field_rejected(self, client, seed):
        response = client.post(
            URL,
            json={
                "payload": make_payload(
                    seed.card_uid, seed.card_roll, seed.merchant_uid
                ),
                "card_uid": seed.card_uid,
            },
        )
        assert response.status_code == 400

    def test_payload_must_be_nonempty_string(self, client):
        assert client.post(URL, json={"payload": ""}).status_code == 400
        assert client.post(URL, json={"payload": 123}).status_code == 400

    def test_oversized_payload_rejected(self, client):
        assert client.post(URL, json={"payload": "A" * 4097}).status_code == 400

    def test_plaintext_card_identity_is_not_a_supported_request(self, client, seed):
        response = client.post(
            URL,
            json={
                "card_uid": seed.card_uid,
                "card_roll_number": seed.card_roll,
                "merchant_id": seed.merchant_uid,
            },
        )
        assert response.status_code == 400
        assert PaymentSession.query.count() == 0

    def test_not_json(self, client, seed):
        r = client.post(URL, data="not json", content_type="text/plain")
        assert r.status_code == 400

    def test_json_array_rejected(self, client, seed):
        response = client.post(URL, json=["payload"])
        assert response.status_code == 400

    def test_invalid_base64(self, client, seed):
        r = post(client, "!!!not_base64!!!")
        assert r.status_code == 400

    def test_payload_too_short(self, client, seed):
        # Less than 12+16 = 28 bytes after decode
        r = post(client, base64.b64encode(b"tooshort").decode())
        assert r.status_code == 400

    def test_inner_not_json(self, client, seed, app):
        """Valid AES-GCM wrapper but plaintext is not JSON."""
        assert post(client, encrypt_plaintext(b"this is not json")).status_code == 400

    def test_invalid_utf8_plaintext_rejected(self, client):
        response = post(client, encrypt_plaintext(b"\xff"))
        assert response.status_code == 400

    def test_plaintext_must_be_json_object(self, client):
        response = post(client, encrypt_plaintext(json.dumps(["not", "an object"]).encode()))
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Tampered ciphertext
# ---------------------------------------------------------------------------


class TestTamperedCiphertext:
    def test_bit_flip_rejected(self, client, seed):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        raw = bytearray(base64.b64decode(p))
        raw[15] ^= 0xFF  # flip byte inside ciphertext
        tampered = base64.b64encode(bytes(raw)).decode()
        r = post(client, tampered)
        assert r.status_code == 400

    def test_tag_corruption_rejected(self, client, seed):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        raw = bytearray(base64.b64decode(p))
        raw[-1] ^= 0xFF  # flip last byte of the tag
        tampered = base64.b64encode(bytes(raw)).decode()
        r = post(client, tampered)
        assert r.status_code == 400

    def test_iv_corruption_rejected(self, client, seed):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        raw = bytearray(base64.b64decode(p))
        raw[0] ^= 0xFF
        assert post(client, base64.b64encode(raw).decode()).status_code == 400

    def test_no_session_created_on_tamper(self, client, seed, app):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        raw = bytearray(base64.b64decode(p))
        raw[12] ^= 0xAA
        tampered = base64.b64encode(bytes(raw)).decode()
        post(client, tampered)
        with app.app_context():
            assert PaymentSession.query.count() == 0


# ---------------------------------------------------------------------------
# Wrong key
# ---------------------------------------------------------------------------


class TestWrongKey:
    def test_wrong_key_rejected(self, client, seed):
        wrong_key = "ff" * 32
        p = make_payload(
            seed.card_uid, seed.card_roll, seed.merchant_uid, key_hex=wrong_key
        )
        r = post(client, p)
        assert r.status_code == 400

    def test_no_session_on_wrong_key(self, client, seed, app):
        p = make_payload(
            seed.card_uid, seed.card_roll, seed.merchant_uid, key_hex="ff" * 32
        )
        post(client, p)
        with app.app_context():
            assert PaymentSession.query.count() == 0

    def test_misformatted_server_key_is_rejected(self, client, seed, app):
        payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        with app.app_context():
            original_key = app.config["CARD_SCAN_KEY"]
            app.config["CARD_SCAN_KEY"] = original_key + " "
            try:
                response = post(client, payload)
            finally:
                app.config["CARD_SCAN_KEY"] = original_key
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# Expired timestamp
# ---------------------------------------------------------------------------


class TestExpiredTimestamp:
    def test_boolean_timestamp_rejected(self, client, seed):
        plaintext = json.dumps(
            {
                "card_uid": seed.card_uid,
                "card_roll_number": seed.card_roll,
                "merchant_id": seed.merchant_uid,
                "ts": True,
            }
        ).encode()
        assert post(client, encrypt_plaintext(plaintext)).status_code == 400

    def test_old_timestamp_rejected(self, client, seed):
        old_ts = int(time.time()) - 60  # 60 s ago
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid, ts=old_ts)
        r = post(client, p)
        assert r.status_code == 422

    def test_future_timestamp_rejected(self, client, seed):
        future_ts = int(time.time()) + 60
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid, ts=future_ts)
        r = post(client, p)
        assert r.status_code == 422

    def test_no_session_on_stale_timestamp(self, client, seed, app):
        p = make_payload(
            seed.card_uid, seed.card_roll, seed.merchant_uid, ts=int(time.time()) - 60
        )
        post(client, p)
        with app.app_context():
            assert PaymentSession.query.count() == 0


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


class TestReplayProtection:
    def test_replay_rejected(self, client, seed):
        iv = secrets.token_bytes(12)
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid, iv=iv)
        r1 = post(client, p)
        assert r1.status_code == 201

        with client.application.app_context():
            session_record = PaymentSession.query.first()
            session_record.status = "completed"
            db.session.commit()

        r2 = post(client, p)
        assert r2.status_code == 409
        assert r2.get_json()["error"] == (
            "This encrypted request has already been used."
        )

    def test_expired_session_does_not_block_the_next_scan(self, client, seed, app):
        first_payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        first_response = post(client, first_payload)
        assert first_response.status_code == 201

        with app.app_context():
            session_record = PaymentSession.query.first()
            session_record.expires_at = utcnow_naive() - timedelta(seconds=1)
            db.session.commit()

        second_payload = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        second_response = post(client, second_payload)
        assert second_response.status_code == 201
        with app.app_context():
            old_session = PaymentSession.query.filter_by(
                session_id=first_response.get_json()["session_id"]
            ).first()
            assert old_session.status == "expired"

    def test_replay_creates_no_extra_session(self, client, seed, app):
        iv = secrets.token_bytes(12)
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid, iv=iv)
        post(client, p)  # first — succeeds
        post(client, p)  # replay — rejected
        with app.app_context():
            assert PaymentSession.query.count() == 1

    def test_same_iv_with_different_ciphertext_is_rejected(self, client, seed, app):
        iv = secrets.token_bytes(12)
        first = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid, iv=iv)
        assert post(client, first).status_code == 201

        with app.app_context():
            PaymentSession.query.first().status = "completed"
            db.session.commit()

        second = encrypt_plaintext(
            json.dumps(
                {
                    "card_uid": seed.card_uid,
                    "card_roll_number": seed.card_roll,
                    "merchant_id": seed.merchant_uid,
                    "ts": int(time.time()),
                    "extra": "different authenticated plaintext",
                }
            ).encode(),
            iv=iv,
        )
        assert second != first
        assert post(client, second).status_code == 409

    def test_authenticated_iv_is_consumed_even_if_identity_is_unknown(
        self, client, seed
    ):
        p = make_payload("unknown-card", seed.card_roll, seed.merchant_uid)
        assert post(client, p).status_code == 404
        assert post(client, p).status_code == 409

    def test_concurrent_requests_cannot_consume_same_iv_twice(
        self, client, seed
    ):
        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)

        def submit():
            with client.application.test_client() as concurrent_client:
                return post(concurrent_client, p).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: submit(), range(2)))

        assert statuses.count(201) == 1
        assert statuses.count(409) == 1


# ---------------------------------------------------------------------------
# Unknown card UID
# ---------------------------------------------------------------------------


class TestUnknownUID:
    def test_unknown_uid_rejected(self, client, seed):
        p = make_payload("FF:FF:FF:FF", seed.card_roll, seed.merchant_uid)
        r = post(client, p)
        assert r.status_code == 404

    def test_no_session_on_unknown_uid(self, client, seed, app):
        p = make_payload("FF:FF:FF:FF", seed.card_roll, seed.merchant_uid)
        post(client, p)
        with app.app_context():
            assert PaymentSession.query.count() == 0


# ---------------------------------------------------------------------------
# Wrong roll number / UID-roll mismatch
# ---------------------------------------------------------------------------


class TestRollNumberValidation:
    def test_wrong_roll_rejected(self, client, seed):
        p = make_payload(seed.card_uid, "WRONGROLL", seed.merchant_uid)
        r = post(client, p)
        assert r.status_code == 404

    def test_no_session_on_wrong_roll(self, client, seed, app):
        p = make_payload(seed.card_uid, "WRONGROLL", seed.merchant_uid)
        post(client, p)
        with app.app_context():
            assert PaymentSession.query.count() == 0


# ---------------------------------------------------------------------------
# Inactive card
# ---------------------------------------------------------------------------


class TestInactiveCard:
    def test_inactive_card_rejected(self, client, seed, app):
        with app.app_context():
            from models import Card

            card = Card.query.filter_by(card_uid=seed.card_uid).first()
            card.is_active = False
            db.session.commit()

        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r = post(client, p)
        assert r.status_code == 403

    def test_no_session_for_inactive_card(self, client, seed, app):
        with app.app_context():
            from models import Card

            card = Card.query.filter_by(card_uid=seed.card_uid).first()
            card.is_active = False
            db.session.commit()

        p = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        post(client, p)
        with app.app_context():
            assert PaymentSession.query.count() == 0


# ---------------------------------------------------------------------------
# Duplicate scan (active session guard) — Req #9
# ---------------------------------------------------------------------------


class TestDuplicateScan:
    def test_second_scan_rejected_while_session_active(self, client, seed):
        p1 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r1 = post(client, p1)
        assert r1.status_code == 201

        p2 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        r2 = post(client, p2)
        assert r2.status_code == 409

    def test_only_one_session_created(self, client, seed, app):
        p1 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        p2 = make_payload(seed.card_uid, seed.card_roll, seed.merchant_uid)
        post(client, p1)
        post(client, p2)
        with app.app_context():
            assert PaymentSession.query.count() == 1
