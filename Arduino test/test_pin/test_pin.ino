/*
TEST - Checks the analog input pin A0.
Reads A0 and prints the value to the serial port (115200 baud), one line per reading:
open the Arduino IDE Serial Monitor or Serial Plotter to see it.

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