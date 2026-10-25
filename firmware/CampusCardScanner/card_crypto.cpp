#include "card_crypto.h"

#include <ESP8266WiFi.h>
#include <base64.h>
#include <bearssl/bearssl.h>
#include <string.h>

namespace {

constexpr size_t kMaxPlaintextBytes = 2048;
constexpr size_t kMaxEnvelopeBytes =
    kMaxPlaintextBytes + CARD_SCAN_ENVELOPE_OVERHEAD;

uint8_t envelope[kMaxEnvelopeBytes];

int hexValue(char character) {
  if (character >= '0' && character <= '9') {
    return character - '0';
  }
  if (character >= 'a' && character <= 'f') {
    return character - 'a' + 10;
  }
  if (character >= 'A' && character <= 'F') {
    return character - 'A' + 10;
  }
  return -1;
}

bool fillSecureIv(uint8_t iv[CARD_SCAN_IV_BYTES]) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }
  // ESP.random(buffer, size) uses the ESP8266 SDK RNG. Wi-Fi must be active
  // because the RNG's entropy source is RF noise while the radio is enabled.
  return ESP.random(iv, CARD_SCAN_IV_BYTES) == iv;
}

}  // namespace

bool parseCardScanKey(const char* keyHex, uint8_t key[CARD_SCAN_KEY_BYTES]) {
  if (key == nullptr) {
    return false;
  }
  memset(key, 0, CARD_SCAN_KEY_BYTES);
  if (keyHex == nullptr || strlen(keyHex) != 64) {
    return false;
  }
  for (size_t index = 0; index < CARD_SCAN_KEY_BYTES; ++index) {
    const int high = hexValue(keyHex[index * 2]);
    const int low = hexValue(keyHex[index * 2 + 1]);
    if (high < 0 || low < 0) {
      memset(key, 0, CARD_SCAN_KEY_BYTES);
      return false;
    }
    key[index] = static_cast<uint8_t>((high << 4) | low);
  }
  return true;
}

bool encryptCardScanPayload(const char* plaintext, size_t plaintextLength,
                            const uint8_t key[CARD_SCAN_KEY_BYTES],
                            char* base64Output, size_t outputCapacity,
                            size_t* base64Length) {
  if (plaintext == nullptr || key == nullptr || base64Output == nullptr ||
      base64Length == nullptr || plaintextLength == 0 ||
      plaintextLength > kMaxPlaintextBytes ||
      outputCapacity < ((plaintextLength + CARD_SCAN_ENVELOPE_OVERHEAD + 2) / 3) * 4 + 1) {
    return false;
  }

  if (!fillSecureIv(envelope)) {
    return false;
  }

  br_aes_ct_ctr_keys aesContext;
  br_aes_ct_ctr_init(&aesContext, key, CARD_SCAN_KEY_BYTES);
  br_gcm_context gcmContext;
  br_gcm_init(&gcmContext, &aesContext.vtable, br_ghash_ctmul32);
  br_gcm_reset(&gcmContext, envelope, CARD_SCAN_IV_BYTES);
  br_gcm_flip(&gcmContext);
  uint8_t* ciphertext = envelope + CARD_SCAN_IV_BYTES;
  memcpy(ciphertext, plaintext, plaintextLength);
  br_gcm_run(&gcmContext, 1, ciphertext, plaintextLength);
  br_gcm_get_tag(
      &gcmContext, ciphertext + plaintextLength);
  memset(&aesContext, 0, sizeof(aesContext));
  memset(&gcmContext, 0, sizeof(gcmContext));

  const size_t envelopeLength =
      CARD_SCAN_IV_BYTES + plaintextLength + CARD_SCAN_TAG_BYTES;
  const size_t expectedBase64Length = ((envelopeLength + 2) / 3) * 4;
  String encoded = base64::encode(envelope, envelopeLength, false);
  memset(envelope, 0, sizeof(envelope));
  if (encoded.length() != expectedBase64Length ||
      encoded.length() >= outputCapacity) {
    base64Output[0] = '\0';
    return false;
  }

  encoded.toCharArray(base64Output, outputCapacity);
  *base64Length = encoded.length();
  return true;
}
