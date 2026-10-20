/*
TEST - This sketch is used to test the TX -> RX acoustic path.
The TX piezo (pin 9) emits a continuous tone at TEST_FREQ, while the RX piezo (pin A0)
is sampled with the fast ADC over a 3 ms window.
Every line sends "vmin<TAB>vmax<TAB>vmax-vmin" to the serial port (115200 baud):
readable with the Arduino IDE Serial Monitor or Serial Plotter.

TEST_FREQ is a free parameter: change it to test the path at any frequency
(for example ~1550 Hz, the dominant loaded resonance measured with the main sketch).

*/
#define PIN_TX 9
#define PIN_RX A0
#define TEST_FREQ 4000  // near the free resonance of the bare 27 mm discs (in the reactor the peak is ~1550 Hz)

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