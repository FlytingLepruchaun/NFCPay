# Campus Card Scanner firmware

ESP8266 NodeMCU + RC522 firmware for the existing Flask `POST /api/card-scan`
contract. It only reads a card and transports an authenticated encrypted scan;
it does not perform payment or database work.

## Hardware and wiring

Use a NodeMCU ESP8266 ESP-12E/ESP-12F and an RC522 wired over SPI:

| RC522 | NodeMCU pin | ESP8266 GPIO |
|---|---|---:|
| SDA / SS | D2 | GPIO4 |
| RST | D1 | GPIO5 |
| SCK | D5 | GPIO14 |
| MOSI | D7 | GPIO13 |
| MISO | D6 | GPIO12 |
| 3.3V | 3V3 | 3.3 V only |
| GND | GND | Ground |

Power the RC522 from **3.3 V only**. Do not connect it to 5 V. Keep SPI wiring
short. `SPI.begin()` uses the ESP8266 hardware SPI pins shown above; D2 is the
chip-select pin and D1 is reset.

## Software and libraries

The project supports Arduino IDE and PlatformIO.

| Dependency | Version | Purpose |
|---|---:|---|
| ESP8266 Arduino core / PlatformIO `espressif8266` | 3.1.2 / 4.2.1 | NodeMCU target, Wi-Fi, HTTP, NTP, BearSSL |
| MFRC522 by Miguel Balboa | 1.4.12 | RC522 SPI reader |
| ArduinoJson | 6.21.5 | Safe plaintext JSON generation and response parsing |
| BearSSL and ESP8266 core Base64 | Core-bundled | AES-256-GCM and standard padded Base64; no separate crypto package |

The AES/GCM API used is BearSSL's bundled `<bearssl/bearssl.h>`:
`br_aes_ct_ctr_init()` with a 32-byte AES key, then `br_gcm_init()`,
`br_gcm_reset()` with a 12-byte IV, `br_gcm_flip()` (required even when no
AAD is supplied), `br_gcm_run()` and `br_gcm_get_tag()` for a 16-byte tag.
The ESP8266 core's `base64::encode(..., false)` emits standard padded Base64
without line breaks. These are mature implementations bundled with the same
ESP8266 core used for the HTTPS BearSSL client; the AES/GCM implementation is
not handwritten. The core's `ESP.random(buffer, size)` fills IV bytes from
the SDK RNG. Its implementation documents that Wi-Fi must be active for the
RF-noise entropy source; firmware only requests IV bytes after Wi-Fi has
connected. It does not use Arduino `random()`, a counter, or the clock for IV
generation.

Sources:

* [ESP8266 Arduino core](https://github.com/esp8266/Arduino)
* [MFRC522 1.4.12](https://github.com/miguelbalboa/rfid)
* [ArduinoJson 6.21.5](https://arduinojson.org/v6/)
* [ESP8266 3.1.2 RNG implementation](https://github.com/esp8266/Arduino/blob/3.1.2/cores/esp8266/Esp.cpp)
* [BearSSL GCM API](https://www.bearssl.org/apidoc/bearssl__aead_8h.html)

## Configuration

Create `config.local.h` beside `config.h` (copy
`config.local.h.example`) and set:

* `WIFI_SSID` and `WIFI_PASSWORD`: local wireless credentials.
* `MERCHANT_ID`: the configured merchant ID, maximum 100 characters.
* `SERVER_URL`: full endpoint ending in `/api/card-scan`.
* `CARD_SCAN_KEY_HEX`: the same 64-hex-character `CARD_SCAN_KEY` used by
  Flask; exactly 32 key bytes. Invalid, short, long, or non-hex values prevent
  scanning; the firmware never pads or truncates keys.
* `DEV_CARD_UID` and `DEV_CARD_ROLL_NUMBER`: temporary, compile-time mapping
  for one test card.

The firmware reads optional `config.local.h` before applying the safe empty
defaults in `config.h`. Keep `config.local.h` private; do not commit real
wireless credentials or a shared AES key. Never use the sample key or sample
credentials from `config.local.h.example`.

### Local HTTP versus production HTTPS

Local development may use a trusted private LAN URL such as
`http://192.168.1.10:5000/api/card-scan`, but must explicitly set
`ALLOW_LOCAL_HTTP_FOR_DEVELOPMENT true`. HTTP sends the encrypted envelope
without TLS and is for isolated development only.

Production must set an `https://.../api/card-scan` URL and configure
`TLS_ROOT_CA_PEM` to the PEM-encoded CA certificate that validates the server's
certificate chain. Obtain the CA certificate from the service operator and
provision it in the private `config.local.h`; do not paste a leaf certificate
that will expire or trust an unverified source. The firmware uses BearSSL
trust anchors and normal certificate verification. It never calls
`setInsecure()`. HTTPS setup fails closed if the CA value is empty or
certificate validation fails.

## Card identity limitation

The project has no agreed physical storage/provisioning format for
`card_roll_number`. `readCardIdentity()` is the isolated identity seam. This
initial firmware supports only an explicitly marked development mapping from
one configured UID to one configured roll number. It does **not** assume a
MIFARE sector/block, authenticate with a default card key, or represent a
production card format. Choose and provision server-side UID mapping, NDEF,
MIFARE data, or another approved mechanism before deployment.

The former `embedded stuff/iot_cbp` sketch and Wi-Fi test were retired because
they included hard-coded credentials and the old sketch used predictable IVs
and an assumed card block. The RC522 smoke-test sketch is retained; use the
supported project here for all scan transport.

## Build and upload

### PlatformIO

From `firmware/platformio`:

```powershell
pio run
pio run --target upload
pio device monitor --baud 115200
```

Set the correct `upload_port` for your operating system/device if PlatformIO
does not discover it automatically.

### Arduino IDE

1. Install Arduino IDE 2.x.
2. In Boards Manager, install **esp8266 by ESP8266 Community**, version 3.1.2.
3. In Library Manager, install **MFRC522 by GithubCommunity**, version 1.4.12,
   and **ArduinoJson**, version 6.21.5.
4. Open `CampusCardScanner.ino` from this folder and create/configure
   `config.local.h`.
5. Select **NodeMCU 1.0 (ESP-12E Module)** and the correct serial port.
6. Verify, upload, then open Serial Monitor at 115200 baud.

The ESP8266 core supplies BearSSL, Base64, SPI, Wi-Fi, HTTP, and NTP; do not
install another GCM/Base64 library.

## Backend and wire protocol

Set Flask `CARD_SCAN_KEY` to the same private 64-character hex value as
`CARD_SCAN_KEY_HEX`. The backend remains authoritative for authentication,
card/merchant validation, replay protection, payment sessions, balances, and
all business rules. The ESP never accesses the database.

The UTF-8 plaintext JSON has exactly these fields and types:

```json
{
  "card_uid": "AA:BB:CC:DD",
  "card_roll_number": "STU001",
  "merchant_id": "merchant1",
  "ts": 1791345600
}
```

Protocol:

```text
complete plaintext JSON
  -> AES-256-GCM (32-byte key, fresh 12-byte IV, 16-byte tag, no AAD)
  -> [IV][ciphertext][tag]
  -> standard padded Base64
  -> POST /api/card-scan
     Content-Type: application/json
     Accept: application/json
     {"payload":"<base64>"}
```

The outer JSON has only `payload`; identity is never sent outside encryption.
NTP synchronization must succeed before the firmware scans. A failed HTTP
request is not automatically retried; remove and rescan the card to create a
new payload with a fresh IV. Holding one card on the reader does not resubmit
it, and a 1.5-second same-UID debounce is also applied.

## Diagnostics and troubleshooting

Set `DEBUG_MODE false` for production logging. Card UIDs are not logged unless
`LOG_CARD_UID true` is deliberately enabled. Keys, passwords, and plaintext
payloads are never printed.

* **Wi-Fi failure:** verify SSID/password and 2.4 GHz coverage; reconnect is
  retried without a reboot.
* **NTP failure:** allow outbound NTP/UDP 123 and DNS; scanning remains
  disabled until Unix time is valid.
* **RC522 does not respond over SPI:** verify 3.3 V, common ground, D1/D2
  selection, and SPI wires; avoid 5 V. Firmware checks the reader's SPI version
  register rather than gating operation on the library's digital self-test,
  which reports false for chip revisions without a known test reference.
* **Invalid key:** provide exactly 64 hex digits matching Flask
  `CARD_SCAN_KEY`; the key is never logged.
* **TLS failure:** verify NTP time, URL hostname, server chain, and provisioned
  CA PEM. Do not disable certificate validation.
* **HTTP 400:** request/envelope or decrypted identity is malformed; verify
  firmware/backend key and schema configuration.
* **HTTP 403:** the card is inactive.
* **HTTP 404:** card UID, roll number, or merchant is not registered/matched.
* **HTTP 409:** the IV was replayed or the merchant already has an active
  payment session; do not retransmit the same encrypted envelope.
* **HTTP 422:** timestamp is outside the backend replay window; restore NTP.
* **HTTP 500:** backend configuration/database failure; inspect Flask server
  logs without exposing shared keys.

## Protocol test and test boundary

The repository test `tests/test_firmware_protocol.py` constructs the documented
IV/ciphertext/tag envelope with an independent AES-GCM implementation, decodes
it as the Flask backend does, and posts it through the actual Flask test
endpoint. This verifies protocol/backend interoperability, not execution of
the ESP binary. The PlatformIO build checks firmware compilation. Physical
reader, radio, NTP, TLS, and live-server behavior still require hardware tests.
