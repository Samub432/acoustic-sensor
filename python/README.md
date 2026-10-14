# python: acquisition and analysis

Scripts to record data from the board and analyse it. Run them **from inside this folder**: they all import `acoustic_common.py` from here.

## Install

```bash
pip install -r ../requirements.txt
python test_common.py      # prints "ok"
python test_split.py       # prints "ok"
```

Python 3.11 or newer (tested with 3.12).

## Files

| File | What it does |
|---|---|
| `acoustic_common.py` | Shared code: port detection, loading and cleaning a CSV, features of one sweep. The scripts don't run without it. |
| `01_data_from_sensor.py` | Records the serial data to a CSV (one file per block). |
| `02_analysis.py` | Spectral plots: last sweep, waterfall, comparison with a baseline file. |
| `03_features.py` | Main analysis: features over time (dashboard), coupling QC, light/dark comparison. |
| `04_realtime_monitor.py` | Live view while mounting the discs. Records nothing. The spectrum is in raw ADC, so its level shows the coupling quality (discs in air: flat line around 10 ADC); if the port is lost the window shows **DISCONNECTED**. |
| `test_common.py`, `test_split.py` | Self-checks of `acoustic_common.py` and of 01 (`--split` file rotation, start command sent on `# READY`). |
| `data/` | Your recordings. |
| `trials/` | Raw data of our validation trials. |

## Typical session

```bash
python 04_realtime_monitor.py                                        # check the signal, then close it
python 01_data_from_sensor.py --out data/my_session/warmup1.csv --duration 300
python 03_features.py --csv data/my_session/warmup1.csv              # GO/NO-GO: peak flat, PEAK >= 40.8 dB (~110 ADC), QC OK
python 01_data_from_sensor.py --out data/my_session/light1.csv --duration 900
python 03_features.py --csv data/my_session/light1.csv --out data/my_session/light1_feat.csv --summary data/my_session/summary.csv --save
```

## Options

| Script | Options |
|---|---|
| 01 | `--port`, `--out`, `--duration` (s), `--split` (s), `--cmd S/B/N/R/X` |
| 02 | `--csv`, `--baseline`, `--show all/sweep/waterfall/baseline`, `--save` |
| 03 | `--csv`, `--out`, `--save`, `--state light/dark/none`, `--settle` (s, default 180), `--summary` |
| 04 | `--port` |

The port is found automatically. With more than one Arduino connected the first one found is used: pass `--port` to choose.

## Good to know

- **One file per block**, named after its condition. 03 reads the state from the name: `light…` -> LIGHT, `dark…` or `night…` -> DARK, anything else -> NONE. For LIGHT and DARK files the first 180 s are excluded from the statistics.
- Names that only *contain* the state, like `sample1_light2.csv`, are read as NONE: pass `--state light` or `--state dark`. 03 warns when the name mentions light/dark but the state is NONE.
- `--summary` collects one row per file and prints the LIGHT − DARK comparison. It needs at least 3 dark blocks between light blocks, all from the same folder.
- Only one program can use the serial port: close the Arduino Serial Monitor and 04 before starting 01.
- `--save` writes the PNG files in the folder you run the script from, not next to the CSV.
- Never open and save a raw CSV in Excel: the rows get quoted and the scripts can no longer read the file.
- How to read the plots? User Guide. Definitions of the metrics? Operating Manual, Ch. 13–14.
