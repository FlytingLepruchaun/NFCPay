#pragma once

#include <stddef.h>
#include <stdint.h>

constexpr size_t CARD_SCAN_KEY_BYTES = 32;
constexpr size_t CARD_SCAN_IV_BYTES = 12;
constexpr size_t CARD_SCAN_TAG_BYTES = 16;
constexpr size_t CARD_SCAN_ENVELOPE_OVERHEAD =
    CARD_SCAN_IV_BYTES + CARD_SCAN_TAG_BYTES;

bool parseCardScanKey(const char* keyHex, uint8_t key[CARD_SCAN_KEY_BYTES]);

bool encryptCardScanPayload(const char* plaintext, size_t plaintextLength,
                            const uint8_t key[CARD_SCAN_KEY_BYTES],
                            char* base64Output, size_t outputCapacity,
                            size_t* base64Length);
