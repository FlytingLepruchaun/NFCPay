#pragma once

#if defined(__has_include)
#if __has_include("config.local.h")
#include "config.local.h"
#endif
#endif

#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD ""
#endif

#ifndef MERCHANT_ID
#define MERCHANT_ID ""
#endif

#ifndef SERVER_URL
#define SERVER_URL "https://example.com/api/card-scan"
#endif

#ifndef CARD_SCAN_KEY_HEX
#define CARD_SCAN_KEY_HEX "REPLACE_WITH_64_HEX_CHAR_KEY"
#endif

#ifndef TLS_ROOT_CA_PEM
#define TLS_ROOT_CA_PEM ""
#endif

#ifndef ALLOW_LOCAL_HTTP_FOR_DEVELOPMENT
#define ALLOW_LOCAL_HTTP_FOR_DEVELOPMENT false
#endif

#ifndef DEBUG_MODE
#define DEBUG_MODE true
#endif

#ifndef LOG_CARD_UID
#define LOG_CARD_UID false
#endif

#ifndef SERIAL_BAUD
#define SERIAL_BAUD 115200
#endif

#ifndef RC522_SS_PIN
#define RC522_SS_PIN D2
#endif

#ifndef RC522_RST_PIN
#define RC522_RST_PIN D1
#endif

#ifndef NTP_SERVER_1
#define NTP_SERVER_1 "pool.ntp.org"
#endif

#ifndef NTP_SERVER_2
#define NTP_SERVER_2 "time.nist.gov"
#endif

#ifndef WIFI_RETRY_INTERVAL_MS
#define WIFI_RETRY_INTERVAL_MS 10000UL
#endif

#ifndef NTP_RETRY_INTERVAL_MS
#define NTP_RETRY_INTERVAL_MS 20000UL
#endif

#ifndef READER_RETRY_INTERVAL_MS
#define READER_RETRY_INTERVAL_MS 5000UL
#endif

#ifndef CARD_DEBOUNCE_MS
#define CARD_DEBOUNCE_MS 1500UL
#endif

#ifndef HTTP_TIMEOUT_MS
#define HTTP_TIMEOUT_MS 8000UL
#endif

#ifndef MAX_IDENTITY_LENGTH
#define MAX_IDENTITY_LENGTH 100
#endif

#ifndef DEV_CARD_UID
#define DEV_CARD_UID ""
#endif

#ifndef DEV_CARD_ROLL_NUMBER
#define DEV_CARD_ROLL_NUMBER ""
#endif
