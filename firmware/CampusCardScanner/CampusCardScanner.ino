#include <Arduino.h>
#include <ArduinoJson.h>
#include <BearSSLHelpers.h>
#include <ESP8266HTTPClient.h>
#include <ESP8266WiFi.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WiFiClientSecureBearSSL.h>
#include <time.h>

#include "card_crypto.h"
#include "card_identity.h"
#include "config.h"

void setup();
void loop();

namespace {

constexpr time_t kMinimumValidUnixTime = 1704067200;
constexpr size_t kMaxPlaintextBytes = 2048;
constexpr size_t kMaxBase64Bytes =
    ((kMaxPlaintextBytes + CARD_SCAN_ENVELOPE_OVERHEAD + 2) / 3) * 4 + 1;
constexpr size_t kMaxRequestBytes = kMaxBase64Bytes + 32;

MFRC522 reader(RC522_SS_PIN, RC522_RST_PIN);
uint8_t cardScanKey[CARD_SCAN_KEY_BYTES];
char plaintextBuffer[kMaxPlaintextBytes];
char encryptedPayload[kMaxBase64Bytes];
char requestBody[kMaxRequestBytes];
char lastScannedUid[31];
bool readerReady = false;
bool configurationReady = false;
bool ntpConfigured = false;
bool wifiAttempted = false;
bool validClockSeen = false;
uint32_t lastWifiAttemptMs = 0;
uint32_t lastNtpAttemptMs = 0;
uint32_t lastReaderAttemptMs = 0;
uint32_t lastScannedAtMs = 0;

void info(const __FlashStringHelper* message) {
  if (DEBUG_MODE) {
    Serial.print(F("[INFO] "));
    Serial.println(message);
  }
}

void error(const __FlashStringHelper* message) {
  Serial.print(F("[ERROR] "));
  Serial.println(message);
}

bool hasNonWhitespace(const char* value) {
  if (value == nullptr || value[0] == '\0' || strlen(value) > MAX_IDENTITY_LENGTH) {
    return false;
  }
  for (const char* cursor = value; *cursor != '\0'; ++cursor) {
    if (*cursor != ' ' && *cursor != '\t' && *cursor != '\r' && *cursor != '\n') {
      return true;
    }
  }
  return false;
}

bool endsWithCardScanPath(const char* url) {
  constexpr char kPath[] = "/api/card-scan";
  const size_t urlLength = strlen(url);
  const size_t pathLength = sizeof(kPath) - 1;
  return urlLength >= pathLength &&
         strcmp(url + urlLength - pathLength, kPath) == 0;
}

bool isHttpsUrl(const char* url) {
  return strncmp(url, "https://", 8) == 0;
}

bool configurationIsValid() {
  if (WIFI_SSID[0] == '\0' || WIFI_PASSWORD[0] == '\0') {
    error(F("Set Wi-Fi credentials in config.h or config.local.h."));
    return false;
  }
  if (!hasNonWhitespace(MERCHANT_ID)) {
    error(F("MERCHANT_ID must be a non-empty value of at most 100 characters."));
    return false;
  }
  if (!endsWithCardScanPath(SERVER_URL)) {
    error(F("SERVER_URL must end with /api/card-scan."));
    return false;
  }
  if (!isHttpsUrl(SERVER_URL)) {
    if (strncmp(SERVER_URL, "http://", 7) != 0 ||
        !ALLOW_LOCAL_HTTP_FOR_DEVELOPMENT) {
      error(F("HTTP is disabled. Enable it only for local development."));
      return false;
    }
  } else if (TLS_ROOT_CA_PEM[0] == '\0') {
    error(F("Configure a trusted CA certificate before using HTTPS."));
    return false;
  }
  if (!parseCardScanKey(CARD_SCAN_KEY_HEX, cardScanKey)) {
    error(F("CARD_SCAN_KEY_HEX must be exactly 64 hexadecimal characters."));
    return false;
  }
  return true;
}

void startWifiAttempt() {
  if (wifiAttempted &&
      static_cast<uint32_t>(millis() - lastWifiAttemptMs) < WIFI_RETRY_INTERVAL_MS) {
    return;
  }
  wifiAttempted = true;
  lastWifiAttemptMs = millis();
  info(F("Connecting to Wi-Fi..."));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void updateNetworkAndClock() {
  if (WiFi.status() != WL_CONNECTED) {
    if (validClockSeen) {
      info(F("Wi-Fi disconnected; waiting for reconnection."));
    }
    validClockSeen = false;
    ntpConfigured = false;
    startWifiAttempt();
    return;
  }

  if (!ntpConfigured) {
    info(F("Wi-Fi connected."));
    configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
    ntpConfigured = true;
    lastNtpAttemptMs = millis();
    info(F("Synchronizing time with NTP..."));
  }

  const time_t now = time(nullptr);
  if (now >= kMinimumValidUnixTime) {
    if (!validClockSeen) {
      validClockSeen = true;
      info(F("Time synchronized; scanner is ready."));
    }
    return;
  }

  validClockSeen = false;
  if (static_cast<uint32_t>(millis() - lastNtpAttemptMs) >= NTP_RETRY_INTERVAL_MS) {
    configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
    lastNtpAttemptMs = millis();
    error(F("NTP has not synchronized yet; scanning remains disabled."));
  }
}

bool scannerIsReady() {
  return WiFi.status() == WL_CONNECTED && validClockSeen &&
         time(nullptr) >= kMinimumValidUnixTime;
}

bool initializeReader() {
  if (readerReady) {
    return true;
  }
  if (lastReaderAttemptMs != 0 &&
      static_cast<uint32_t>(millis() - lastReaderAttemptMs) <
          READER_RETRY_INTERVAL_MS) {
    return false;
  }
  lastReaderAttemptMs = millis();
  info(F("Initializing RC522."));
  SPI.begin();
  reader.PCD_Init();
  delay(4);
  const byte version = reader.PCD_ReadRegister(MFRC522::VersionReg);
  if (version == 0x00 || version == 0xFF) {
    error(F("RC522 did not respond over SPI; check pin mapping and power."));
    return false;
  }
  readerReady = true;
  if (DEBUG_MODE) {
    Serial.print(F("[INFO] RC522 firmware version: 0x"));
    Serial.println(version, HEX);
  }
  info(F("RC522 initialized."));
  return true;
}

bool formatUid(char* output, size_t outputSize) {
  if (reader.uid.size == 0 || reader.uid.size > sizeof(reader.uid.uidByte)) {
    return false;
  }
  size_t used = 0;
  output[0] = '\0';
  for (byte index = 0; index < reader.uid.size; ++index) {
    const int written = snprintf(
        output + used, outputSize - used, index == 0 ? "%02X" : ":%02X",
        reader.uid.uidByte[index]);
    if (written < 0 || static_cast<size_t>(written) >= outputSize - used) {
      output[0] = '\0';
      return false;
    }
    used += static_cast<size_t>(written);
  }
  return true;
}

bool createPlaintext(const char* uid, const char* rollNumber, uint32_t timestamp,
                     size_t* plaintextLength) {
  if (!hasNonWhitespace(uid) || !hasNonWhitespace(rollNumber) ||
      !hasNonWhitespace(MERCHANT_ID)) {
    return false;
  }

  StaticJsonDocument<512> document;
  document["card_uid"] = uid;
  document["card_roll_number"] = rollNumber;
  document["merchant_id"] = MERCHANT_ID;
  document["ts"] = timestamp;
  const size_t written =
      serializeJson(document, plaintextBuffer, sizeof(plaintextBuffer));
  if (written == 0 || written >= sizeof(plaintextBuffer)) {
    return false;
  }
  *plaintextLength = written;
  return true;
}

void printSafeError(const char* message) {
  if (message == nullptr) {
    return;
  }
  Serial.print(F(": "));
  for (size_t index = 0; message[index] != '\0' && index < 180; ++index) {
    const unsigned char character = static_cast<unsigned char>(message[index]);
    Serial.print(static_cast<char>(
        character >= 0x20 && character <= 0x7E ? character : '?'));
  }
}

void handleHttpResponse(int statusCode, HTTPClient& http) {
  if (statusCode == HTTP_CODE_CREATED) {
    info(F("Card scan accepted. Payment session created."));
    return;
  }
  if (statusCode < 0) {
    Serial.print(F("[ERROR] HTTP transport failed: "));
    Serial.println(http.errorToString(statusCode));
    return;
  }

  Serial.print(F("[ERROR] Card scan rejected. HTTP "));
  Serial.print(statusCode);
  const String responseBody = http.getString();
  StaticJsonDocument<512> responseDocument;
  if (deserializeJson(responseDocument, responseBody) == DeserializationError::Ok) {
    printSafeError(responseDocument["error"] | static_cast<const char*>(nullptr));
  }
  Serial.println();
}

void postWithClient(WiFiClient& client) {
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  if (!http.begin(client, SERVER_URL)) {
    error(F("Could not initialize the HTTP request."));
    return;
  }

  http.addHeader(F("Content-Type"), F("application/json"));
  http.addHeader(F("Accept"), F("application/json"));
  info(F("Sending encrypted card scan."));
  const int statusCode = http.POST(
      reinterpret_cast<uint8_t*>(requestBody), strlen(requestBody));
  handleHttpResponse(statusCode, http);
  http.end();
}

void sendEncryptedScan(const char* uid, const char* rollNumber) {
  const time_t now = time(nullptr);
  if (!scannerIsReady() || now < kMinimumValidUnixTime) {
    error(F("Scan cancelled because Wi-Fi or synchronized time is unavailable."));
    return;
  }

  size_t plaintextLength = 0;
  if (!createPlaintext(uid, rollNumber, static_cast<uint32_t>(now),
                       &plaintextLength)) {
    error(F("Could not construct a valid card-scan JSON object."));
    return;
  }

  size_t encodedLength = 0;
  info(F("Encrypting payload."));
  if (!encryptCardScanPayload(
          plaintextBuffer, plaintextLength, cardScanKey, encryptedPayload,
          sizeof(encryptedPayload), &encodedLength)) {
    error(F("AES-GCM encryption or secure IV generation failed."));
    memset(plaintextBuffer, 0, sizeof(plaintextBuffer));
    return;
  }
  memset(plaintextBuffer, 0, sizeof(plaintextBuffer));

  const int requestLength =
      snprintf(requestBody, sizeof(requestBody), "{\"payload\":\"%s\"}",
               encryptedPayload);
  memset(encryptedPayload, 0, sizeof(encryptedPayload));
  if (requestLength <= 0 ||
      static_cast<size_t>(requestLength) >= sizeof(requestBody) ||
      static_cast<size_t>(requestLength) > encodedLength + 16) {
    error(F("Encrypted request does not fit the configured buffer."));
    memset(requestBody, 0, sizeof(requestBody));
    return;
  }

  if (isHttpsUrl(SERVER_URL)) {
    BearSSL::X509List trustAnchors(TLS_ROOT_CA_PEM);
    BearSSL::WiFiClientSecure secureClient;
    secureClient.setTimeout(HTTP_TIMEOUT_MS);
    secureClient.setTrustAnchors(&trustAnchors);
    postWithClient(secureClient);
  } else {
    WiFiClient localDevelopmentClient;
    localDevelopmentClient.setTimeout(HTTP_TIMEOUT_MS);
    postWithClient(localDevelopmentClient);
  }
  memset(requestBody, 0, sizeof(requestBody));
}

void processDetectedCard() {
  if (!reader.PICC_ReadCardSerial()) {
    error(F("Card UID could not be read."));
    reader.PICC_HaltA();
    reader.PCD_StopCrypto1();
    return;
  }

  char uid[31] = {};
  char rollNumber[MAX_IDENTITY_LENGTH + 1] = {};
  if (!formatUid(uid, sizeof(uid))) {
    error(F("Card UID has an unsupported length."));
  } else if (DEBUG_MODE && LOG_CARD_UID) {
    Serial.print(F("[INFO] UID: "));
    Serial.println(uid);
  }

  if (uid[0] != '\0') {
    const uint32_t now = millis();
    const bool isRecentDuplicate =
        strcmp(uid, lastScannedUid) == 0 &&
        static_cast<uint32_t>(now - lastScannedAtMs) < CARD_DEBOUNCE_MS;
    if (isRecentDuplicate) {
      info(F("Duplicate card ignored by local debounce."));
    } else {
      strncpy(lastScannedUid, uid, sizeof(lastScannedUid) - 1);
      lastScannedUid[sizeof(lastScannedUid) - 1] = '\0';
      lastScannedAtMs = now;
      if (!scannerIsReady()) {
        error(F("Card detected, but Wi-Fi or NTP is not ready; remove and rescan."));
      } else if (!readCardIdentity(uid, rollNumber, sizeof(rollNumber))) {
        error(F("No development UID-to-roll mapping matched this card."));
      } else {
        info(F("Card detected."));
        sendEncryptedScan(uid, rollNumber);
        memset(rollNumber, 0, sizeof(rollNumber));
      }
    }
  }

  reader.PICC_HaltA();
  reader.PCD_StopCrypto1();
}

}  // namespace

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(50);
  info(F("Booting Campus Card Scanner."));
  configurationReady = configurationIsValid();
  if (!configurationReady) {
    error(F("Configuration is invalid; card scans are disabled."));
    return;
  }

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  startWifiAttempt();
  initializeReader();
}

void loop() {
  if (!configurationReady) {
    delay(10);
    return;
  }
  updateNetworkAndClock();
  if (!initializeReader()) {
    delay(10);
    return;
  }
  if (scannerIsReady() && reader.PICC_IsNewCardPresent()) {
    processDetectedCard();
  }
  delay(10);
}
