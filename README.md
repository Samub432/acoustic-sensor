# Acoustic Sensor — a piezoelectric probe for a photosynthetic bioreactor

An Arduino Uno drives a piezoelectric transmitter through a frequency sweep and
reads the response with a second piezo on the opposite side of the vessel. The
sweep response changes when gas bubbles are present in the liquid, so the
acoustic signal is used as a **non-invasive proxy for the oxygen released by an
aquatic plant** (*Elodea*) under light.

A camera pointed at the same vessel counts the bubbles optically, which gives an
independent reference to correlate the acoustic features against.

> **Status: experimental.** The acoustic energy (RMS) and the variability of the
> spectral centroid are measured quantities. Reading the *direction* of a
> centroid shift as a change in bubble size is a working hypothesis, not a
> calibrated result — the scripts label it as such wherever it appears. The
> categorical "biological status" verdicts were disabled for the same reason:
> their thresholds were never calibrated against a dissolved-oxygen probe.
> Every file is also checked for coupling artefacts: when peak amplitude and
> centroid move together, the file is flagged instead of being read as biology.

## Hardware

| | |
|---|---|
| Board | Arduino Uno R3 |
| Transmitter | piezoelectric disc on pin **9** (PWM) |
| Receiver | piezoelectric disc on pin **A0** (ADC) |
| Status LED | pin 13 (built-in) |
| Serial | 115200 baud |

Measured characteristics of the loaded system (piezo + vessel wall + water):
dominant resonance around **1550/1583 Hz**, secondary clusters near 2120 Hz and
2454–2612 Hz, noise floor around **10 ADC counts**. Above ~3 kHz there is no
signal, so the sweep covers **1000–3500 Hz in 60 logarithmic steps** — the same
number of steps concentrated where the signal actually is.

Serial line format, one per frequency step:

```
F,AMPL,TIMESTAMP,MODE
```

`AMPL` is the peak-to-peak ADC amplitude (0–1023) over at least three periods of
the tone. `MODE` is `S` (sweep), `P` (passive), `B` (burst) or `N` (noise).

## Install

```bash
git clone <this-repo>
cd acustic-sensor
pip install -r requirements.txt
```

Upload `Acoustic_Sensor/Acoustic_Sensor.ino` to the board with the Arduino IDE.
The sketch boots **idle** and waits for the start command sent by script 01, so
opening the port does not by itself start a sweep.

`05_bubble_count.py` decodes video ~3x faster when `ffmpeg` is on `PATH`
(otherwise it falls back to OpenCV). Set the `FFMPEG` environment variable if it
is installed somewhere else.

## Workflow

The scripts are numbered in the order they are used. All of them live in
`python/` and are run from that directory.

### 1. Record a session

```bash
python 01_data_from_sensor.py --port COM3 --out data/session.csv
```

The port is auto-detected if `--port` is omitted. `--duration 300` stops after
300 s; without it, the acquisition runs until Ctrl+C. `--split 900` rotates the
output every 900 s into numbered files (`session_001.csv`, `session_002.csv`,
...). `--cmd S` sends a single command to Arduino and exits.

Record a **baseline** the same way, on water only, into its own file — the
analysis compares against it.

Light/dark experiments follow the convention **one file = one light block**:
switch the light at the start of a file and name it `light*.csv` or
`night*.csv` / `dark*.csv`. Step 3 reads the light state from the file name.

### 2. Look at the spectra

```bash
python 02_analysis.py --csv data/session.csv
python 02_analysis.py --csv data/session.csv --baseline data/baseline.csv --show baseline
```

Spectra only: the last-sweep frequency response, the waterfall plot of all
sweeps over time, and the difference against a baseline run. All features are
computed in step 3. `--save` writes PNGs instead of opening windows.

### 3. Extract the features

```bash
python 03_features.py --csv data/session.csv --out features.csv
```

Per sweep: `centroid_hz`, `peak_freq_hz`, `peak_ampl_db` (raw peak ADC in dB, a
coupling check), `rms_abs_db` and `high_freq_ratio`, plus a **slow** 10-sweep
moving average of each and `centroid_sigma_hz`, the rolling variability of the
centroid. `--save` writes the dashboard as PNG.

The light state comes from the file name (`--state light|dark|none` overrides
it). `--settle 180` sets the seconds excluded at the start of a light/dark
file. `--summary blocks.csv` writes one row per file into a summary CSV and
prints the light/dark comparison over all the blocks in it. Every run prints:

```
  FILE       light1_2.csv   (state: LIGHT, from file name)
  SWEEPS     72 used / 90   (first 180 s excluded: settling)
  QC         OK        r(peak, centroid) = +0.05  (n=72)
  CENTROID   1593.6 Hz    median rolling sigma 20.2 Hz
  RMS        32.10 dB     PEAK 41.04 dB     HFR 0.196
  TREND      centroid -0.79 Hz/min    RMS +0.011 dB/min
```

- **QC** — correlation *r* between peak amplitude and centroid over the file.
  A change in the probe coupling moves both together, so `WARNING`
  (|r| >= 0.6, a heuristic threshold) means the file is likely driven by
  coupling, not by biology; such files are left out of the comparison.
- **Settling** — the light switches at the start of each file, and the first
  180 s are a coupling/thermal transient. They are excluded from all statistics
  of light/dark files (the plots shade them grey).
- **Bracketed comparison** — each dark block is compared with the mean of the
  light blocks right before and after it. A linear drift across the session
  cancels out, whereas light vs. next dark would read the drift as a light
  effect. Blocks from different sessions are never compared.

```bash
for f in trials/06_elodea_2/light?_2.csv trials/06_elodea_2/night?_2.csv; do
    python 03_features.py --csv "$f" --summary blocks_e2.csv --save
done
```

### 4. Watch it live (optional)

```bash
python 04_realtime_monitor.py
```

Real-time spectrum, centroid and RMS while Arduino transmits — no intermediate
CSV file. The monitor sends the start command itself, and computes the same
features as step 3. The port is auto-detected if `--port` is omitted.

### 5. Count the bubbles in the video

```bash
python 05_bubble_count.py video.mp4                     # draw the ROI with the mouse
python 05_bubble_count.py video.mp4 --roi 320,540,60,20 # reuse a known ROI
python 05_bubble_count.py video.mp4 --debug             # tune the parameters visually
```

Background subtraction inside a narrow horizontal "counting line" just above the
emission point, with nearest-neighbour tracking so each bubble is counted once.
Writes a CSV with `time_s,bubbles_per_second`, to be correlated with the
acoustic features of step 3.

## Repository layout

```
Acoustic_Sensor/     Arduino sketch (sweep generation + ADC acquisition)
Arduino test/        standalone sketches used to test pins and the TX/RX pair
python/
  01_..05_*.py       the pipeline described above
  acoustic_common.py shared code: port detection, CSV loading, per-sweep features
  test_split.py      self-check of the --split file rotation
  data/              working directory for new sessions
  trials/            recorded experiment data, one folder per trial
requirements.txt
```

## Data

`python/trials/` holds the CSVs of the experiments already performed:
baseline characterisation runs, a sparkling-water test used as a positive
control with known bubbling, and four *Elodea* light/dark cycles (05–08).
`08_elodea_4/alternate_4.csv` alternates light and dark within one file, so the
automatic light state of step 3 does not apply to it.

**The raw videos are not in this repository.** They are tens of gigabytes and are
excluded by `.gitignore`; only the `bubbles_*.csv` counts extracted from them are
tracked, inside the trial folders. Ask the authors for the source footage if you
need to re-run step 5.

## Self-check

Runs without hardware:

```bash
python 05_bubble_count.py --selftest   # bubble tracking self-check
python test_split.py                   # --split file rotation self-check
```
