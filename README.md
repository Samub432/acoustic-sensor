# Acoustic Bubble Sensor for Photobioreactors

A cheap, non-invasive way to see gas activity in a culture flask: put a piezo on one side, another on the other side, play some tones through the liquid and listen to what comes out.

Built by the students of **iGEM 2026 Uni-Padua-IT**.

---

## Why we built this

The idea started from a space problem. If you grow photosynthetic organisms in a sealed life-support bioreactor, you want to know how much gas they're producing, but every time you open the vessel or stick a probe in it you risk contamination, both for the crew and, in the planetary protection sense, for wherever you are. So we asked: can we measure it *through the wall*?

It turns out the same question matters on Earth. Most teaching labs and small research groups can't put a lab-grade dissolved oxygen probe in every flask. A sensor that never touches the culture and can be copied for every flask on the shelf seemed worth trying.

## How it works

Two 27 mm piezo discs sit on opposite faces of a T25 culture flask (through-transmission). The Arduino sweeps 60 tones from 1 to 3.5 kHz on the transmitter, and the receiver picks up how much of each tone gets through. One sweep takes about 5 seconds, followed by 5 seconds of passive listening: a new sweep about every 10 seconds.

Gas bubbles crossing the acoustic path change how sound travels through the liquid. Bubbles appearing and disappearing make the received spectrum flicker from one sweep to the next, and the Python pipeline turns that flicker into numbers you can track over time (spectral centroid, sweep-to-sweep variation, RMS energy, peak amplitude).

## Documentation
**Everything you need, from setup to data analysis, can be found in the documentation. If anything's missing or you need further clarification, don't hesitate to contact us!**

- **User Guide**: how to run a session and read the dashboard.
- **Operating Manual**: build, wiring, firmware, measurement model, error catalogue, validation results.
- **Wiki**: https://2026.igem.wiki/uni-padua-it/

## Hardware

Total component cost is about **€42.72**, see the **Operating Manual** for further detail!

All prices are from Amazon, to show how easy the parts are to get, specialized retailers may charge differently. We built everything around an **ELEGOO UNO R3 Super Starter Kit (€37.35)** to show how cheap and accessible the components are: every item marked **\*** in the price column comes in that kit.

| # | Component | Specifications | Qty | Price [€] | Notes |
|---|---|---|---|---|---|
| E1 | Arduino Uno R3 or compatible clone | ATmega328P, 16 MHz, 10-bit ADC | 1 | \* | Clones with a CH340 USB converter need its driver |
| E2 | Piezoelectric disc | Ø 27 mm, brass substrate, PZT, two-wire | 2 (+3 spare) | 15.59 (pack of 20) | Solder joints are fragile |
| E3 | LM358 op-amp | DIP-8 | 1 (+2 spare) | 3.81 | |
| E4 | Ceramic capacitor | 100 nF | 1 | \* | AC input coupling |
| E5 | Resistor | 100 kΩ | 1 | \* | Input |
| E6 | Resistor | 1 MΩ | 2 | \* | In parallel: 500 kΩ feedback |
| E7 | Resistor | 10 kΩ | 3 | \* | Two for the bias divider, one to protect A0 |
| E8 | Resistor | 100 Ω | 1 | \* | In series with TX |
| E9 | Breadboard + jumpers | | 1 | \* | Move to stripboard or PCB later |
| E10 | USB A-B cable | | 1 | \* | |

**Total: €42.72** (kit + LM358 + the two piezo discs actually used, pro-rated from the pack of 20).

Also needed, not in the total: a 3D-printed holder (optional but recommended) and a couplant (vaseline or ultrasound gel).

Wiring in short:

- **TX:** Arduino pin 9 -> 100 Ω -> transmitting piezo.
- **RX:** receiving piezo -> 100 nF -> 100 kΩ -> LM358 pin 2, with 2 × 1 MΩ in parallel as feedback (pin 2 -> pin 1). Pin 3 biased at 2.5 V with a 10k/10k divider. Pin 1 -> 10 kΩ -> A0.
- **Power:** 5 V on pin 8, GND on pin 4. All grounds common (Arduino, op-amp, cold leads of both piezos).

The full step-by-step build, with the multimeter checks, is in the Operating Manual.

## Getting started

You need Python 3.10+ and the Arduino IDE.

```bash
cd python
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r ../requirements.txt

# self-checks, both should pass
python test_common.py
python test_split.py
```

Upload `Acoustic_Sensor/Acoustic_Sensor.ino` to the board. Open the serial monitor at 115200 baud and you should see `# READY`.

A typical session looks like this:

```bash
# watch the signal while you mount the discs (records nothing)
python 04_realtime_monitor.py

# record a 15-min block
python 01_data_from_sensor.py --out data/my_session/light1.csv --duration 900

# analyse it
python 03_features.py --csv data/my_session/light1.csv --summary data/my_session/summary.csv --save
```
See the **User guide** for the entire process.

## What's in here

| File | What it does |
|---|---|
| Acoustic_Sensor/Acoustic_Sensor.ino | Measuring firmware. Has to be on the board for every session. |
| Arduino test/test_pin/test_pin.ino, Arduino test/test_TX_TR/test_TX_TR.ino | Hardware checks before the first session or after a repair. |
| python/acoustic_common.py | Shared functions. Scripts 01–04 won't run without it. |
| python/01_data_from_sensor.py | Records one block of data to CSV. |
| python/02_analysis.py | Spectral plots: last sweep, waterfall, comparison with a baseline. |
| python/03_features.py | Main analysis: features over time, quality check, light/dark comparison. |
| python/04_realtime_monitor.py | Live view during mounting and warm-up. |
| python/trials/ | Raw data from our validation trials, so you can rerun everything. |
| docs/ | User Guide and Operating Manual (PDF). |


## If you build one

We'd genuinely love to hear about it, especially if something in the manual didn't make sense or didn't work for you. Open an issue, that's the most useful feedback we can get.

## License

Software (firmware and Python): MIT\
Hardware (circuit, BOM, 3D-printed holder): CERN-OHL-P-2.0\
Documentation and data (manuals, figures, python/trials/): CC BY 4.0

## Team

iGEM 2026 Uni-Padua-IT, University of Padova.\
Team email: mutans.biologia@unipd.it