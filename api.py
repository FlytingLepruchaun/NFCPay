"""
Flask API — card-scan endpoint.

Hardware flow (see PROTOCOL.md for full specification):
  RC522 → ESP (encrypt AES-256-GCM) → POST /api/card-scan → Flask (decrypt/verify)
  → validate card → create PaymentSession.

The endpoint NEVER accepts raw card data. All card identity is obtained
only after successful authenticated decryption.
"""

import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from auth import login_required, role_required
from models import Card, Merchant, PaymentSession, UsedNonce, db
from time_utils import utcnow_naive

api_bp = Blueprint("api", __name__, url_prefix="/api")

PAYMENT_SESSION_TTL = timedelta(minutes=5)
MAX_ENCRYPTED_PAYLOAD_LENGTH = 4096


class InvalidPayloadError(ValueError):
    """The encrypted request is malformed or fails authenticated validation."""


class StaleTimestampError(ValueError):
    """The authenticated payload timestamp is outside the accepted window."""


class ReplayError(ValueError):
    """The authenticated request reuses an IV that has already been accepted."""


class ServerConfigurationError(RuntimeError):
    """The server cannot decrypt payloads because its key is misconfigured."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decrypt_payload(raw_b64: Any) -> tuple[dict[str, Any], bytes]:
    """
    Decode and decrypt the AES-256-GCM blob from the ESP.

    Returns the parsed inner JSON dict on success.
    Raises ValueError for format/decryption errors.
    """
    if not isinstance(raw_b64, str) or len(raw_b64) > MAX_ENCRYPTED_PAYLOAD_LENGTH:
        raise InvalidPayloadError("Payload must be a bounded base64 string.")

    try:
        blob = base64.b64decode(raw_b64, validate=True)
    except (ValueError, TypeError):
        raise InvalidPayloadError("Payload is not valid base64.") from None
    if base64.b64encode(blob).decode("ascii") != raw_b64:
        raise InvalidPayloadError("Payload is not valid base64.")

    if len(blob) < 12 + 16:
        raise InvalidPayloadError("Payload is too short to contain an IV and tag.")

    iv = blob[:12]
    tag = blob[-16:]
    ciphertext = blob[12:-16]
    combined = ciphertext + tag  # AESGCM expects ciphertext+tag together

    key_hex = current_app.config.get("CARD_SCAN_KEY")
    if (
        not isinstance(key_hex, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", key_hex) is None
    ):
        raise ServerConfigurationError("The card-scan key is misconfigured.")
    try:
        key_bytes = bytes.fromhex(key_hex)
    except (TypeError, ValueError):
        raise ServerConfigurationError("The card-scan key is misconfigured.") from None

    if len(key_bytes) != 32:
        raise ServerConfigurationError("The card-scan key must be 32 bytes.")

    try:
        aesgcm = AESGCM(key_bytes)
        plaintext = aesgcm.decrypt(iv, combined, None)
    except InvalidTag:
        raise InvalidPayloadError("Encrypted payload authentication failed.") from None

    try:
        inner = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidPayloadError("Decrypted payload is not valid JSON.") from None

    if not isinstance(inner, dict):
        raise InvalidPayloadError("Decrypted payload must be a JSON object.")

    return inner, iv


def _check_replay(inner: dict[str, Any], iv: bytes) -> None:
    """
    Enforce:
      1. Timestamp within REPLAY_WINDOW_SECONDS of server clock.
      2. IV has not been seen before.
    Raises ValueError on failure. A timestamp-valid IV is committed immediately.
    """
    window = current_app.config.get("REPLAY_WINDOW_SECONDS", 30)

    ts = inner.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise InvalidPayloadError("'ts' must be an integer Unix timestamp.")

    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    if abs(current_timestamp - ts) > window:
        raise StaleTimestampError("Payload timestamp is outside the accepted window.")

    iv_hex = iv.hex()
    nonce = UsedNonce()
    nonce.nonce = iv_hex
    nonce.used_at = utcnow_naive()
    db.session.add(nonce)
    try:
        # The unique nonce constraint makes this reservation atomic across requests.
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if UsedNonce.query.filter_by(nonce=iv_hex).first():
            raise ReplayError("This encrypted request has already been used.") from None
        raise
    except SQLAlchemyError:
        db.session.rollback()
        if UsedNonce.query.filter_by(nonce=iv_hex).first():
            raise ReplayError("This encrypted request has already been used.") from None
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@api_bp.route("/card-scan", methods=["POST"])
def card_scan():
    """
    Receive an AES-256-GCM encrypted card-scan payload from the ESP/NodeMCU.

    Expected body:
        { "payload": "<base64(IV[12] + ciphertext + tag[16])>" }

    On success, creates a PaymentSession with status 'awaiting_amount'.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"payload"}:
        return jsonify({"error": "Request must contain only the 'payload' field."}), 400

    # --- Step 1: Decrypt and authenticate ---
    try:
        inner, iv = _decrypt_payload(data["payload"])
    except InvalidPayloadError as exc:
        return jsonify({"error": str(exc)}), 400
    except ServerConfigurationError:
        current_app.logger.exception("Card-scan decryption configuration failed.")
        return jsonify({"error": "Card-scan service is not configured."}), 500

    # --- Step 2: Replay protection ---
    try:
        _check_replay(inner, iv)
    except InvalidPayloadError as exc:
        return jsonify({"error": str(exc)}), 400
    except StaleTimestampError as exc:
        return jsonify({"error": str(exc)}), 422
    except ReplayError as exc:
        return jsonify({"error": str(exc)}), 409
    except SQLAlchemyError:
        current_app.logger.exception("Failed to reserve card-scan nonce.")
        return jsonify({"error": "Unable to process card scan."}), 500

    # --- Step 3: Extract and validate required fields ---
    card_uid = inner.get("card_uid")
    card_roll_number = inner.get("card_roll_number")
    merchant_id_str = inner.get("merchant_id")

    required_fields = (card_uid, card_roll_number, merchant_id_str)
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 100
        for value in required_fields
    ):
        return jsonify(
            {"error": "Card UID, roll number, and merchant ID are required strings."}
        ), 400

    # --- Step 4: Validate card (Req #5) ---
    card = Card.query.filter_by(card_uid=card_uid).first()
    if not card:
        return jsonify({"error": "Card UID not found."}), 404

    if not card.is_active:
        return jsonify({"error": "Card is inactive."}), 403

    if card.card_roll_number != card_roll_number:
        return jsonify({"error": "Card roll number mismatch."}), 404

    student = card.student
    if not student:
        return jsonify({"error": "Card is not associated with any student."}), 404

    # --- Step 5: Validate merchant (Req #7) ---
    merchant = Merchant.query.filter_by(merchant_id=merchant_id_str).first()
    if not merchant:
        return jsonify({"error": "Merchant not found."}), 404

    # --- Step 6: Guard against duplicate active sessions (Req #9) ---
    now = utcnow_naive()
    try:
        expired_session = PaymentSession.query.filter(
            PaymentSession.merchant_id == merchant.id,
            PaymentSession.status.in_(["awaiting_amount", "processing"]),
            PaymentSession.expires_at <= now,
        ).first()
        if expired_session is not None:
            db.session.execute(
                update(PaymentSession)
                .where(
                    PaymentSession.merchant_id == merchant.id,
                    PaymentSession.status.in_(["awaiting_amount", "processing"]),
                    PaymentSession.expires_at <= now,
                )
                .values(status="expired")
            )
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to expire old payment sessions.")
        return jsonify({"error": "Unable to create a payment session."}), 500

    active = PaymentSession.query.filter(
        PaymentSession.merchant_id == merchant.id,
        PaymentSession.status.in_(["awaiting_amount", "processing"]),
    ).first()
    if active:
        return jsonify(
            {
                "error": (
                    "Merchant already has an active payment session. "
                    "Complete, cancel, or wait for it to expire before scanning again."
                )
            }
        ), 409

    # --- Step 7: Create payment session ---
    session_id = str(uuid.uuid4())
    new_session = PaymentSession()
    new_session.session_id = session_id
    new_session.student_id = student.id
    new_session.merchant_id = merchant.id
    new_session.status = "awaiting_amount"
    new_session.created_at = now
    new_session.expires_at = now + PAYMENT_SESSION_TTL
    db.session.add(new_session)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if UsedNonce.query.filter_by(nonce=iv.hex()).first():
            return jsonify(
                {"error": "This encrypted request has already been used."}
            ), 409
        active = PaymentSession.query.filter(
            PaymentSession.merchant_id == merchant.id,
            PaymentSession.status.in_(["awaiting_amount", "processing"]),
        ).first()
        if active:
            return jsonify(
                {
                    "error": (
                        "Merchant already has an active payment session. "
                        "Complete, cancel, or wait for it to expire before "
                        "scanning again."
                    )
                }
            ), 409
        current_app.logger.exception("Integrity error while creating payment session.")
        return jsonify({"error": "Unable to create a payment session."}), 500
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while creating payment session.")
        return jsonify({"error": "Unable to create a payment session."}), 500

    return jsonify(
        {
            "message": "Payment session created successfully.",
            "session_id": session_id,
            "student_name": student.name,
        }
    ), 201


@api_bp.route("/merchant/session-status", methods=["GET"])
@login_required
@role_required("merchant")
def check_session_status():
    """
    Polled by the merchant browser (~1 s interval) to detect a new card scan.
    Returns the oldest active 'awaiting_amount' session for this merchant.

    The merchant identity comes from the authenticated server-side session.
    """
    merchant_id = session.get("user_id")
    merchant = db.session.get(Merchant, merchant_id)
    if merchant is None:
        return jsonify({"error": "Merchant account is unavailable."}), 401

    now = utcnow_naive()
    try:
        stale_session = PaymentSession.query.filter(
            PaymentSession.merchant_id == merchant.id,
            PaymentSession.status.in_(["awaiting_amount", "processing"]),
            PaymentSession.expires_at <= now,
        ).first()
        if stale_session is not None:
            db.session.execute(
                update(PaymentSession)
                .where(
                    PaymentSession.merchant_id == merchant.id,
                    PaymentSession.status.in_(["awaiting_amount", "processing"]),
                    PaymentSession.expires_at <= now,
                )
                .values(status="expired")
            )
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to expire polled payment sessions.")
        return jsonify({"error": "Unable to check payment status."}), 500

    latest = (
        PaymentSession.query.filter(
            PaymentSession.merchant_id == merchant.id,
            PaymentSession.status == "awaiting_amount",
            PaymentSession.expires_at > now,
        )
        .order_by(PaymentSession.created_at.desc())
        .first()
    )

    if latest:
        return jsonify(
            {
                "status": "awaiting_amount",
                "session_id": latest.session_id,
                "student_name": latest.student.name,
            }
        )

    return jsonify({"status": "waiting"})
