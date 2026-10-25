#include <Arduino.h>

void setup() {
  Serial.begin(115200);
  Serial.println(
      F("Retired credential-bearing test. Configure firmware/config.local.h.example instead."));
}

void loop() {
  delay(1000);
}
