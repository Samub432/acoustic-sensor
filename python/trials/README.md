# trials: validation data

Raw CSV files of our validation trials, one folder per trial and one file per block, so every result can be rerun with the scripts in `python/`.

## Index

| Folder | Trial | Files |
|---|---|---|
| `01_baseline_water_failed/` | Baseline, water only (failed run) | `baseline1–3` |
| `02_baseline_water_ok/` | Baseline, water only | `baseline1–3` |
| `03_baseline_new_hardware/` | Baseline, water only, new hardware | `baseline1–3` |
| `04_sparkling_water/` | Sparkling water | `baseline_for_sparkling`, `sparkling`, `sparkling_pt2` |
| `05_elodea_1/` | Elodea, run 1 | `warmup`, `baseline_E_nothing`, `light1–2`, `dark1–3`, `bubbles_light1–2_ok` |
| `06_elodea_2/` | Elodea, run 2 | `warmup_2`, `baseline_E_2`, `light1–6_2`, `night1–6_2`, `bubbles_light1–6_2` |
| `07_elodea_3/` | Elodea, run 3 | `warmup_3`, `light1–4_3`, `night1–4_3`, `straw` |
| `08_elodea_4/` | Elodea, run 4 | `warmup_4`, `warmup_4_2`, `light1–2_4`, `night1–2_4`, `alternate_4` |
| `09_Experiment_Nostoc_23092026/` | Nostoc, 23/09/2026 | subfolders `BLANK_23.9.26/`, `SAMPLE1_23.9.26/`, `SAMPLE2_23.9.26/`: warm-up + numbered blocks (`blank_001–003`, `sample1_001–006`, `sample2_001–006`) |
| `10_Experiment_Nostoc_5102026/` | Nostoc, 05/10/2026 | subfolders `Blank 5-10-2026/` (`blank_light`, `blank_dark`), `Sample1 5-10-2026/` (`sample1_light1–3`, `sample1_dark1–3`), `Sample2 5-10-2026/` (`sample2_light1–2`, `sample2_dark1–2`) |
| `12_Experiment_Nostoc_12102026/` | Nostoc, 12/10/2026 | `blank_light`, `Blank_dark`, `sample1__light1`, `sample1_light2–3`, `sample1_dark1–3` |
| `13_Experiment_Nostoc_13102026/` | Nostoc, 13/10/2026: set-up checks | `setup_lampON/lampOff_charged/uncharged`, `setup_lamp_charged/uncharged_light/dark` |
