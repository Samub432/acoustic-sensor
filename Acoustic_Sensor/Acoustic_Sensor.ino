// Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
// SPDX-License-Identifier: MIT
/*  
ACOUSTIC BIOREACTOR - PROGRAM FOR ARDUINO UNO R3
Transmission of the sweep and acquisition of the data

CONNECTION
Piezoelectric sensor - Transmission -> Pin 9 (PWM)
Piezoelectric sensor - Receiver     -> Pin A0 (ADC)
Led status                          -> Pin 13 (built-in) (FOR DEBUG)

SERIAL PROTOCOL (Arduino -> Python)
Every data line is: "F,AMPL,ARDUINO_MS,MODE\n"; lines starting with '#' are messages
F          = frequency in Hz (0 for passive/noise samples)
AMPL       = peak-to-peak amplitude ADC (vmax - vmin, from 0 to 1023) measured over a
             window of at least 3 periods of the tone (NOT an average of N samples)
ARDUINO_MS = millis() of Arduino
MODE       = S(sweep)/P(passive)/B(burst)/N(noise)
RAW mode does not follow this format: one raw ADC value per line, between
"# RAW_START" and "# RAW_END" (01 does not save these lines)

MODALITY
1. SWEEP  -> scan from F_MIN to F_MAX in N_STEPS steps
2. BURST  -> fixed frequency continuous tone, log peak-to-peak ADC
3. NOISE  -> TX silence, listen only (noise baseline)
4. RAW    -> TX silence, dump of 512 raw ADC samples per block

Modality is selected by Python over serial: S/B/N/R, X = stop
*/

// PIN 
#define PIN_TX       9    // tone() output to the TX piezo
#define PIN_RX      A0    // RX piezo, through the LM358 front end
#define PIN_LED     13    // on while running, blinks off for 10 ms at every tx_off()

// SWEEP PARAMETERS
// F_MAX = 3500 Hz: the useful band is 1-3 kHz (dominant loaded resonance
// ~1550/1583 Hz, secondaries ~2120 and ~2454/2558/2612 Hz); above ~3 kHz there is
// only the noise floor (~10 ADC). Keeping the 60 log steps inside 1-3.5 kHz gives
// ~4x the frequency resolution of a 1-20 kHz sweep, where the signal actually is.
#define F_MIN       1000  // Hz - minimum frequency
#define F_MAX       3500  // Hz - maximum frequency (useful band)
// Wide-band diagnostic mode: uncomment to re-characterize the system (PWM Uno limit ~31kHz)
// #define F_MAX    20000
#define N_STEPS       60  // number of frequency steps
#define DWELL_MS      80  // ms per frequency: 40 ms settling, then the measurement, then the rest of the wait

//  BURST PARAMETERS
#define BURST_FREQ   8000 // Hz - frequency burst (default)

#define BAUD        115200  // must match BAUD_RATE in the Python scripts

// GLOBAL STATE
char  mode        = 'I';   // current modality: I(idle)/S/B/N/R - starts idle, waits for Python's start command
unsigned long sweep_interval = 5000; // ms of pause between sweeps, spent listening passively (P lines)


void setup() {
  Serial.begin(BAUD);
  pinMode(PIN_TX,  OUTPUT);
  pinMode(PIN_LED, OUTPUT);

  // ADC prescaler 16 instead of the default 128: ~77 kHz sampling instead of ~9.6 kHz,
  // enough samples per period to catch the peaks of a few-kHz tone
  ADCSRA = (ADCSRA & 0xF8) | 0x04; // prescaler 16

  // the first readings after boot come out too high: throw away 5
  for (int i = 0; i < 5; i++) analogRead(PIN_RX);

  // let the power supply settle
  delay(200);

  // short warm-up tone at F_MIN, otherwise the very first tone after boot comes out wrong
  tone(PIN_TX, F_MIN);
  delay(150);
  noTone(PIN_TX);

  // boot header: on "# READY" Python sends 'S' and the sweep starts
  Serial.println("# ACOUSTIC BIOREACTOR v1.0");
  Serial.println("# freq_hz,ampl_adc,arduino_ms,mode");
  Serial.println("# READY");

  digitalWrite(PIN_LED, HIGH);
}


void loop() {

  if (Serial.available()) {
    char cmd = Serial.read();
    parse_command(cmd);
  }

  // run the current mode; each run_* returns when it is done or a command arrives
  switch (mode) {
    case 'I': /* idle: TX off, do nothing until Python sends a command */ break;
    case 'S': run_sweep();  break;
    case 'B': run_burst();  break;
    case 'N': run_noise();  break;
    case 'R': run_raw();    break;
  }
}


// commands from Python, one character each; unknown characters are ignored

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
      // stop everything: TX off, back to idle
      mode = 'I';
      tx_off();
      Serial.println("# TX OFF");
      break;
  }
}


// MODALITY 1: FREQUENCY SWEEP 
// Generate a frequency, wait for stabilization, measure, advance

void run_sweep() {

  // log-spaced steps: the same relative resolution at every frequency
  // f[i] = F_MIN * (F_MAX/F_MIN)^(i/N_STEPS), so the last step is just below F_MAX (~3427 Hz)
  float log_min = log((float)F_MIN);
  float log_max = log((float)F_MAX);
  float log_step = (log_max - log_min) / N_STEPS;

  // 01 starts a new --split file only on this line, so a sweep is never cut in two
  Serial.print("# SWEEP_START,");
  Serial.println(millis());

  for (int i = 0; i < N_STEPS; i++) {

    // only X and N stop a sweep halfway; any other character received here is read and lost
    if (Serial.available()) {
      char cmd = Serial.read();
      if (cmd == 'X' || cmd == 'N') {
        parse_command(cmd);
        return; // stop sweep
      }
    }

    float f = exp(log_min + log_step * i);
    unsigned int freq = (unsigned int)f;

    tone(PIN_TX, freq);

    // the piezos need time to reach a steady vibration: measure only after 40 ms
    delay(40);

    int ampl = measure_amplitude(freq);

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

  tx_off();

  Serial.print("# SWEEP_END,");
  Serial.println(millis());

  // pause between sweeps: TX silent, listen passively (bubble noise, interference);
  // 03 uses these P lines for the passive-noise check
  unsigned long t0 = millis();
  while (millis() - t0 < sweep_interval) {
    if (Serial.available()) {
      parse_command(Serial.read());
      return;
    }
    // one ADC reading every 100 ms
    int passive = analogRead(PIN_RX);
    Serial.print(0);        // freq=0: nothing is being played
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
// continuous tone at BURST_FREQ, peak-to-peak ADC logged every ~50 ms:
// useful to watch a single frequency in real time

void run_burst() {
  tone(PIN_TX, BURST_FREQ);

  unsigned long t0 = millis();

  while (mode == 'B') {

    if (Serial.available()) {
      parse_command(Serial.read());
      break;
    }

    int ampl = measure_amplitude(BURST_FREQ);

    Serial.print(BURST_FREQ);
    Serial.print(',');
    Serial.print(ampl);
    Serial.print(',');
    Serial.print(millis());
    Serial.print(',');
    Serial.println('B');

    delay(50);
  }
}


// MODALITY 3: NOISE (TX off, listening only)
// records whatever the RX piezo hears: bubbles, pump, background

void run_noise() {
  tx_off();

  while (mode == 'N') {

    if (Serial.available()) {
      parse_command(Serial.read());
      break;
    }

    int sample = analogRead(PIN_RX);
    // serial output buffer full: drop the sample rather than block the loop
    if (Serial.availableForWrite() > 20) {
      Serial.print(0);
      Serial.print(',');
      Serial.print(sample);
      Serial.print(',');
      Serial.print(millis());
      Serial.print(',');
      Serial.println('N');
    }
    delayMicroseconds(400); // at most ~2.5 kHz; in practice the serial link sets the real rate
  }
}


// MODALITY 4: RAW (TX off, blocks of 512 raw ADC values, one per line)
// for short diagnostic captures only: no script in python/ reads this format yet

void run_raw() {
  tx_off();

  Serial.println("# RAW_START");
  for (int i = 0; i < 512; i++) {
    Serial.println(analogRead(PIN_RX));
  }
  Serial.println("# RAW_END");

  delay(200); // pause between raw bursts
}

// Peak-to-peak amplitude (vmax - vmin, 0..1023) of the RX signal at a given frequency.
// The window has to cover a few full periods: a shorter one can miss the true max or min
// and underestimate the amplitude. 3 periods are ~3 ms at 1 kHz, ~0.15 ms at 20 kHz.
int measure_amplitude(unsigned int freq) {
  unsigned long period_us = (freq > 0) ? (1000000UL / freq) : 1000UL;
  unsigned long window_us = 3UL * period_us;
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

// silence the TX; the LED blinks off for 10 ms as a visible sign

void tx_off() {
  noTone(PIN_TX);
  digitalWrite(PIN_TX, LOW);
  digitalWrite(PIN_LED, LOW);
  delay(10);
  digitalWrite(PIN_LED, HIGH);
}
