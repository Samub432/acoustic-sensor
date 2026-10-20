# Acoustic_Sensor: measuring firmware

Firmware for the Arduino Uno. It has to be on the board for every session.

## What it does

- Plays a sweep of 60 tones, log-spaced from 1000 to 3500 Hz (generated grid ≈ 999-3427 Hz), on **pin 9** (TX disc).
- For each tone it measures the **peak-to-peak amplitude** on **A0** (RX disc, through the LM358 front end), 0-1023 ADC.
- After each sweep it listens for 5 s with the transmitter off (`P` lines, one every ≈ 100 ms).
- Result: one sweep about every **10 s**.

The board starts idle. `01_data_from_sensor.py` and `04_realtime_monitor.py` send `S` automatically when they open the port.

## Upload

1. Arduino IDE -> open `Acoustic_Sensor.ino` -> *Tools -> Board: Arduino Uno* -> select the port -> **Upload**.
2. Serial Monitor at **115200 baud**: you should see `# READY`. Send `S` and data lines should scroll.
3. Close the Serial Monitor before running the Python scripts: only one program can use the port.

## Commands (one character over serial)

| Command | Mode | What happens |
|---|---|---|
| `S` | SWEEP | Normal measuring mode: sweep + 5 s passive listening, repeated |
| `B` | BURST | Fixed 8000 Hz tone, amplitude every 50 ms |
| `N` | NOISE | Transmitter off, listening only |
| `R` | RAW | Blocks of 512 raw ADC samples (not saved by the Python logger) |
| `X` | TX off | Switches the transmitter off |

## Output

Data lines: `freq_hz,ampl_adc,arduino_ms,mode` with mode `S` (sweep), `P` (passive), `B`, `N`.
Marker lines start with `#`: `# ACOUSTIC BIOREACTOR v1.0`, `# freq_hz,ampl_adc,arduino_ms,mode`, `# READY`, `# MODE:SWEEP` / `# MODE:BURST` / `# MODE:NOISE` / `# MODE:RAW`, `# TX OFF`, `# SWEEP_START,<ms>`, `# SWEEP_END,<ms>`.
RAW mode sends one raw ADC value per line between `# RAW_START` and `# RAW_END`.

## Main constants

| Constant | Value |
|---|---|
| `F_MIN` / `F_MAX` | 1000 / 3500 Hz |
| `N_STEPS` | 60 |
| `DWELL_MS` | 80 ms per tone (40 ms settle + ≤ 4 ms measurement + 40 ms wait) |
| `sweep_interval` | 5000 ms of passive listening |
| `BAUD` | 115200 |

## Good to know

- Do not change the `# READY` line: `01_data_from_sensor.py` sends `S` when it sees it, and `python/acoustic_common.py` uses it to detect board resets.
- Full details: Operating Manual, Ch. 11-12.