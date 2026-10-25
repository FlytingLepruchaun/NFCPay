"""Interoperability check for the firmware's documented card-scan envelope."""

import base64
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from conftest import TEST_KEY_HEX
from esp_simulator import encrypt_payload


def test_simulated_firmware_envelope_is_accepted_by_flask(client, seed):
    payload = encrypt_payload(
        seed.card_uid,
        seed.card_roll,
        seed.merchant_uid,
        TEST_KEY_HEX,
    )

    envelope = base64.b64decode(payload, validate=True)
    assert len(envelope) >= 12 + 16
    iv = envelope[:12]
    plaintext = AESGCM(bytes.fromhex(TEST_KEY_HEX)).decrypt(iv, envelope[12:], None)
    decoded = json.loads(plaintext.decode("utf-8"))
    assert set(decoded) == {
        "card_uid",
        "card_roll_number",
        "merchant_id",
        "ts",
    }
    assert decoded["card_uid"] == seed.card_uid
    assert decoded["card_roll_number"] == seed.card_roll
    assert decoded["merchant_id"] == seed.merchant_uid
    assert type(decoded["ts"]) is int

    response = client.post(
        "/api/card-scan",
        json={"payload": payload},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 201
