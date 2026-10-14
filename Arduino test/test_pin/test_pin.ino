// Copyright 2026 Samuele Bettiol and the iGEM 2026 Uni-Padua-IT team
// SPDX-License-Identifier: MIT
/*
TEST - This sketch is used to test the analog input pin of Arduino. 
It reads the value from the analog pin and sends it to the serial port. 
The value can be read using the Arduino IDE Serial Monitor or any other serial terminal program.

*/
#define PIN_RX A0
void setup() {
  Serial.begin(115200);
}
void loop() {
  int v = analogRead(PIN_RX);
  Serial.println(v);
  delay(20); // ~50 readings per second, readable by eye
}