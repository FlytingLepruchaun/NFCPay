#include <Arduino.h>

void setup() {
  Serial.begin(115200);
  Serial.println(
      F("Retired insecure sketch. Use firmware/CampusCardScanner/CampusCardScanner.ino."));
}

void loop() {
  delay(1000);
}
