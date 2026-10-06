# data: your recordings

Empty in the repository. `01_data_from_sensor.py` writes here (default: `data/session.csv`) and creates missing folders.

Suggested layout:

```
data/
└── 2026-10-10_elodea_r1/      one folder per session (nothing moved between blocks)
    ├── log.txt                times, temperatures, lamp, couplant, anything unusual
    ├── warmup1.csv            warm-up files, 5 min each
    ├── light1.csv             one file per block, named after its condition
    ├── dark1.csv
    └── ...
```

Keep the raw CSV files untouched and work on copies, we learned from that error.