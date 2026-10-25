"""
ESP/NodeMCU Card-Scan Simulator
================================
Generates a real AES-256-GCM encrypted payload (identical to what the
physical ESP8266 would send) and POSTs it to the Flask /api/card-scan
endpoint.

This is the ONLY supported way to simulate a card scan during development.
It exercises the full: encrypt → HTTP → decrypt → validate → session path.

Usage
-----
    python esp_simulator.py \\
        --card-uid "AA:BB:CC:DD" \\
        --roll "student1" \\
        --merchant "merchant1" \\
        --url "http://127.0.0.1:5000/api/card-scan"

The key must be supplied with --key or through CARD_SCAN_KEY.
"""

import argparse
import base64
import json
import os
import secrets
import time

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv


def encrypt_payload(
    card_uid: str, card_roll_number: str, merchant_id: str, key_hex: str
) -> str:
    """
    Build and encrypt a card-scan payload exactly as the ESP would.

    Returns the base64-encoded string to send in {"payload": "..."}.
    """
    plaintext = json.dumps(
        {
            "card_uid": card_uid,
            "card_roll_number": card_roll_number,
            "merchant_id": merchant_id,
            "ts": int(time.time()),  # UTC Unix timestamp
        }
    ).encode("utf-8")

    try:
        key_bytes = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError("The card-scan key must be valid hexadecimal.") from exc
    if len(key_bytes) != 32:
        raise ValueError("The card-scan key must contain exactly 32 bytes.")
    iv = secrets.token_bytes(12)  # 96-bit random IV per message

    aesgcm = AESGCM(key_bytes)
    # AESGCM.encrypt returns ciphertext + 16-byte tag concatenated
    ct_and_tag = aesgcm.encrypt(iv, plaintext, None)

    blob = iv + ct_and_tag  # IV || ciphertext || tag
    return base64.b64encode(blob).decode("ascii")


def simulate_card_scan(
    card_uid: str,
    card_roll_number: str,
    merchant_id: str,
    server_url: str,
    key_hex: str,
) -> None:
    payload_b64 = encrypt_payload(card_uid, card_roll_number, merchant_id, key_hex)

    print(f"[simulator] Sending encrypted card-scan to {server_url}")
    print(f"[simulator] card_uid        = {card_uid}")
    print(f"[simulator] card_roll_number= {card_roll_number}")
    print(f"[simulator] merchant_id     = {merchant_id}")

    resp = requests.post(
        server_url,
        json={"payload": payload_b64},
        timeout=10,
    )

    print(f"[simulator] HTTP {resp.status_code}")
    try:
        print(f"[simulator] Response: {resp.json()}")
    except requests.exceptions.JSONDecodeError:
        print(f"[simulator] Raw response: {resp.text}")


if __name__ == "__main__":
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    parser = argparse.ArgumentParser(
        description="Simulate an ESP/NodeMCU card scan (encrypted)."
    )
    parser.add_argument(
        "--card-uid", required=True, help="Card UID (e.g. 'AA:BB:CC:DD')"
    )
    parser.add_argument(
        "--roll", required=True, help="Card roll number stored in the DB"
    )
    parser.add_argument(
        "--merchant", required=True, help="Merchant ID (e.g. 'merchant1')"
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:5000/api/card-scan",
        help="Flask endpoint URL",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("CARD_SCAN_KEY"),
        help=(
            "AES-256 key as 64-char hex (default: CARD_SCAN_KEY environment variable)"
        ),
    )
    args = parser.parse_args()
    if args.key is None:
        parser.error("Set CARD_SCAN_KEY or provide --key.")

    simulate_card_scan(
        card_uid=args.card_uid,
        card_roll_number=args.roll,
        merchant_id=args.merchant,
        server_url=args.url,
        key_hex=args.key,
    )
