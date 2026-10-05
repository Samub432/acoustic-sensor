#define PIN_TX 9
#define PIN_RX A0
#define TEST_FREQ 4000  // near the resonance of the 27mm discs

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TX, OUTPUT);
  ADCSRA = (ADCSRA & 0xF8) | 0x04;  // fast ADC (~77 kHz)
  tone(PIN_TX, TEST_FREQ);          // TX always on
}

void loop() {
  int vmax = 0;
  int vmin = 1023;

  unsigned long t0 = micros();
  while ((unsigned long)(micros() - t0) < 3000UL) {
    int v = analogRead(PIN_RX);
    if (v > vmax) vmax = v;
    if (v < vmin) vmin = v;
  }

  Serial.print(vmin);
  Serial.print('\t');
  Serial.print(vmax);
  Serial.print('\t');
  Serial.println(vmax - vmin);
  delay(20);
}