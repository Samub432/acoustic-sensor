/*  
ACOUSTIC BIOREACTOR - PROGRAM FOR ARDUINO UNO R3
Transmission of the sweep and acquisition of the data

CONNECTION
Piezoelectric sensor - Transmission -> Pin 9 (PWM)
Piezoelectric sensor - Receiver     -> Pin A0 (ADC)
Led status                          -> Pin 13 (built-in) (FOR DEBUG)

SERIAL PROTOCOL (to send data to python)
Every line has: "F,AMPL,TIMESTAMP,MODE\n"
F         = frequency
AMPL      = peak-to-peak amplitude ADC (vmax - vmin, from 0 to 1023) measured over a
            window of at least 3 periods of the tone (NOT an average of N samples)
TIMESTAMP = millis() of Arduino
MODE      = S(sweep)/P(passive)/B(burst)/N(noise)
RAW mode does not follow this format: one raw ADC value per line, between
"# RAW_START" and "# RAW_END"

MODALITY
1. SWEEP  -> scan from F_MIN to F_MAX in N_STEPS steps
2. BURST  -> fixed frequency continuous tone, log peak-to-peak ADC
3. NOISE  -> TX silence, listen only (noise baseline)
4. RAW    -> TX silence, dump of 512 raw ADC samples per block

Modality is selected by Python over serial: S/B/N/R, X = stop
*/

// PIN 
#define PIN_TX       9    // exit PWM (= Pulse Width Modulation) to Piezoelectric sensor - transmission
#define PIN_RX      A0    // input ADC piezoelectric sensor - receiver
#define PIN_LED     13    // Control LED 

// SWEEP PARAMETERS
// F_MAX = 3500 Hz: measured useful band is 1-3 kHz (dominant loaded resonance is
// ~1550/1583 Hz, secondaries ~2120 and ~2454/2558/2612 Hz); above ~3 kHz there is
// only noise floor (~10 ADC),
// so scanning up to 20 kHz was wasted time. We are concentrating the same 60 log steps in
// 1-3.5 kHz raises frequency resolution ~4x where the signal actually is.
#define F_MIN       1000  // Hz - minimum frequency
#define F_MAX       3500  // Hz - maximum frequency (useful band)
// Wide-band diagnostic mode: uncomment to re-characterize the system (PWM Uno limit ~31kHz)
// #define F_MAX    20000
#define N_STEPS       60  // number of frequency steps
#define DWELL_MS      80  // ms for every frequency (stabilization + plotting)

//  BURST PARAMETERS
#define BURST_FREQ   8000 // Hz - frequency burst (default)

// BAUD RATE (For Arduino)
#define BAUD        115200

// GLOBAL STATE
char  mode        = 'I';   // current modality: I(idle)/S/B/N/R - starts idle, waits for Python's start command
unsigned long sweep_interval = 5000; // ms between sweep 


void setup() {
  Serial.begin(BAUD);
  pinMode(PIN_TX,  OUTPUT);
  pinMode(PIN_LED, OUTPUT);

  // ADC: prescaler at 16 -> ~77kHz sample rate (default 125kHz/10bit)
  // made samples more faster for signals with high frequency
  ADCSRA = (ADCSRA & 0xF8) | 0x04; // prescaler 16

  // discard first 5 samples, because the first sample is always higher than the others, it is a systematic error of the ADC
  for (int i = 0; i < 5; i++) analogRead(PIN_RX);

  // waiting for power supply stabilization
  delay(200);

  //For the problem of the first tone, send a short tone at F_MIN
  tone(PIN_TX, F_MIN);
  delay(150);
  noTone(PIN_TX);

  // header CSV for Python
  Serial.println("# ACOUSTIC BIOREACTOR v1.0");
  Serial.println("# freq_hz,ampl_adc,timestamp_ms,mode");
  Serial.println("# READY");

  digitalWrite(PIN_LED, HIGH);
}


void loop() {

  // Arduino see if python has send some command, always
  if (Serial.available()) {
    char cmd = Serial.read();
    parse_command(cmd);
  }

  // selector of state, state machine
  switch (mode) {
    case 'I': /* idle: TX off, do nothing until Python sends a command */ break;
    case 'S': run_sweep();  break;
    case 'B': run_burst();  break;
    case 'N': run_noise();  break;
    case 'R': run_raw();    break;
  }
}


// decode of the instruction send by python

void parse_command(char cmd) {
  switch (cmd) {
    case 'S':
      mode = 'S';
      Serial.println("# MODE:SWEEP");
      break;
    case 'B':
      mode = 'B';
      Serial.println("# MODE:BURST");
      break;
    case 'N':
      mode = 'N';
      tx_off();
      Serial.println("# MODE:NOISE");
      break;
    case 'R':
      mode = 'R';
      Serial.println("# MODE:RAW");
      break;
    case 'X':
      // total stop of transmission TX
      mode = 'I';
      tx_off();
      Serial.println("# TX OFF");
      break;
  }
}


// MODALITY 1: FREQUENCY SWEEP 
// Generate a frequency, wait for stabilization, measure, advance

void run_sweep() {

  // logarithmic step calculation (is more useful than linear calculation for acoustics)
  // f[i] = F_MIN * (F_MAX/F_MIN)^(i/N_STEPS)
  float log_min = log((float)F_MIN);
  float log_max = log((float)F_MAX);
  float log_step = (log_max - log_min) / N_STEPS;

  // marker start sweep for Python
  Serial.print("# SWEEP_START,");
  Serial.println(millis());

  for (int i = 0; i < N_STEPS; i++) {

    // if there are interruption by Python, interrupt all the process
    if (Serial.available()) {
      char cmd = Serial.read();
      if (cmd == 'X' || cmd == 'N') {
        parse_command(cmd);
        return; // stop sweep
      }
    }

    // calculate current frequency (log scale)
    float f = exp(log_min + log_step * i);
    unsigned int freq = (unsigned int)f;

    // generation on the piezoelectric sensor TX
    tone(PIN_TX, freq);

    // mechanical stabilization of the piezoelectric sensor
    // first 40ms are skipped
    delay(40);

    // measure the peak-to-peak amplitude of the signal at the current frequency
    int ampl = measure_amplitude(freq);

    // waiting for the dwell
    delay(DWELL_MS - 40);

    // output CSV: freq,ampl,timestamp,mode
    Serial.print(freq);
    Serial.print(',');
    Serial.print(ampl);
    Serial.print(',');
    Serial.print(millis());
    Serial.print(',');
    Serial.println('S');
  }

  // end sweep - silence TX
  tx_off();

  Serial.print("# SWEEP_END,");
  Serial.println(millis());

  // Pause between sweeps
  // During the pause, listen passively (useful for bubble noise)
  unsigned long t0 = millis();
  while (millis() - t0 < sweep_interval) {
    if (Serial.available()) {
      parse_command(Serial.read());
      return;
    }
    // passive sampling every 100ms
    int passive = analogRead(PIN_RX);
    Serial.print(0);        // freq=0 for passive hearing
    Serial.print(',');
    Serial.print(passive);
    Serial.print(',');
    Serial.print(millis());
    Serial.print(',');
    Serial.println('P');    // P = Passive
    delay(100);
  }
}


// MODALITY 2: BURST
// continuously BURST_FREQ, log peak-to-peak ADC every 50ms
// Good for the monitoration in real time at static freq 

void run_burst() {
  tone(PIN_TX, BURST_FREQ);

  unsigned long t0 = millis();

  while (mode == 'B') { 

    if (Serial.available()) {
      parse_command(Serial.read());
      break;
    }

    //peak-to-peak over a window of at least 3 periods
    int ampl = measure_amplitude(BURST_FREQ);

    Serial.print(BURST_FREQ);
    Serial.print(',');
    Serial.print(ampl);
    Serial.print(',');
    Serial.print(millis());
    Serial.print(',');
    Serial.println('B');

    delay(50); // 20 Hz log rate
  }
}


// MODALITY 3: NOISE (pure listening, TX off)
// Captures passive acoustic noise from the reactor
// Includes: bubble noise, pump, background

void run_noise() {
  tx_off();

  while (mode == 'N') {

    if (Serial.available()) {
      parse_command(Serial.read());
      break;
    }

    int sample = analogRead(PIN_RX);
    if (Serial.availableForWrite() > 20) {
      Serial.print(0);
      Serial.print(',');
      Serial.print(sample);
      Serial.print(',');
      Serial.print(millis());
      Serial.print(',');
      Serial.println('N');
    }
    delayMicroseconds(400); // ~2.5kHz sample rate
  }
}


// MODALITY 4: RAW  raw ADC dump at full speed
// Only for short acquisitions (serial buffer becomes saturated)
// In Python: read with read_until or circular buffer

void run_raw() {
  tx_off();

  // send 512 sample raw, then wait
  Serial.println("# RAW_START");
  for (int i = 0; i < 512; i++) {
    Serial.println(analogRead(PIN_RX));
  }
  Serial.println("# RAW_END");

  delay(200); // pause between raw bursts
}

// Measure the peak-to-peak amplitude (vmax - vmin, 0..1023) of the signal at a given frequency
// The window must be long enough to capture several periods of the signal, otherwise the amplitude will be underestimated
// it is necessary because the piezoelectric sensor has a mechanical inertia and the signal is not immediately stable:
// at 1kHz 3 periods are about 3ms, at 20kHz they are about 0.15ms
int measure_amplitude(unsigned int freq) {
  unsigned long period_us = (freq > 0) ? (1000000UL / freq) : 1000UL;
  unsigned long window_us = 3UL * period_us;   // the minimum window is 3 periods of the signal, to capture the amplitude correctly
  if (window_us < 1500UL) window_us = 1500UL;  // minimum: many samples even at high frequencies
  if (window_us > 4000UL) window_us = 4000UL;  // ceiling: don't slow down too much at 1 kHz

  int vmax = 0;
  int vmin = 1023;
  unsigned long t_start = micros();
  while ((unsigned long)(micros() - t_start) < window_us) {
    int v = analogRead(PIN_RX);
    if (v > vmax) vmax = v;
    if (v < vmin) vmin = v;
  }
  return vmax - vmin;   // peak-to-peak amplitude, 0..1023
}

// UTILITY: TX off

void tx_off() {
  noTone(PIN_TX);
  digitalWrite(PIN_TX, LOW);
  digitalWrite(PIN_LED, LOW);
  delay(10);
  digitalWrite(PIN_LED, HIGH);
}
