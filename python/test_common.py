# Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""Self-check for acoustic_common loading/features (run: python test_common.py)."""
import tempfile, csv, io, contextlib, math
from pathlib import Path
import numpy as np
import pandas as pd
from acoustic_common import load_and_segment, extract_feature_single_sweep, block_metrics, passive_noise_adc

# same log formula as the firmware: f[i] = exp(log_min + log_step * i), cast to int
F_MIN, F_MAX, N_STEPS = 1000, 3500, 60
FREQS = [int(math.exp(math.log(F_MIN) + (math.log(F_MAX) - math.log(F_MIN)) / N_STEPS * i))
         for i in range(N_STEPS)]
HEADER = ['pc_timestamp', 'freq_hz', 'ampl_adc', 'arduino_ms', 'mode', 'raw_line']


def run_rows(n_sweeps, ampl=100, header=True, last_points=N_STEPS):
    """One Arduino run: optional reset header, then n_sweeps sweeps; arduino_ms restarts from 0."""
    rows = [[0, 0, 0, 0, 'META', '# ACOUSTIC BIOREACTOR v1.0'],
            [0, 0, 0, 0, 'META', '# READY']] if header else []
    ms = 0
    for s in range(n_sweeps):
        n = last_points if s == n_sweeps - 1 else N_STEPS
        for i, f in enumerate(FREQS[:n]):
            ms += 80
            a = ampl + (i % 7) * 10          # some in-band dynamics
            rows.append([0, f, a, ms, 'S', f"{f},{a},{ms},S"])
    return rows


def load(rows):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.csv"
        with p.open('w', newline='') as fh:
            csv.writer(fh).writerows([HEADER] + rows)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweeps = load_and_segment(str(p))
        return sweeps, out.getvalue()


# a) two runs appended to the same CSV -> only the last run, monotonic t_sec
sweeps, log = load(run_rows(3, ampl=100) + run_rows(2, ampl=300))
assert len(sweeps) == 2, len(sweeps)
assert all(s['ampl_adc'].min() >= 300 for s in sweeps)
t = np.concatenate([s['t_sec'].values for s in sweeps])
assert np.all(np.diff(t) > 0)
assert "2 runs in the file" in log, log

# b) no header (--split file 2+) -> nothing dropped as stale
sweeps, log = load(run_rows(3, header=False))
assert len(sweeps) == 3 and "[LOAD]" not in log, log

# c) no 'S' lines -> [] without exceptions
sweeps, log = load([[0, 0, 0, 0, 'META', '# ACOUSTIC BIOREACTOR v1.0'], [0, 0, 0, 0, 'META', '# READY']])
assert sweeps == [] and "[WARN]" in log, log

# d) truncated last sweep (30 points) -> dropped
sweeps, log = load(run_rows(3, last_points=30))
assert len(sweeps) == 2 and all(len(s) == N_STEPS for s in sweeps)

# e) flat sweep (dead sensor) -> all features NaN
feat = extract_feature_single_sweep(np.array(FREQS), np.full(N_STEPS, 12))
assert all(np.isnan(v) for v in feat.values()), feat
# and a live sweep is not NaN
feat = extract_feature_single_sweep(np.array(FREQS), np.array([10 + (i % 7) * 20 for i in range(N_STEPS)]))
assert not np.isnan(feat['centroid_hz']), feat

# f) the -180 dB min bin must not ring into a fake peak 3 bins away (savgol edge coefs are negative)
p = int(np.argmin(np.abs(np.array(FREQS) - 1550)))   # true peak ~1550 Hz
t = int(np.argmin(np.abs(np.array(FREQS) - 2500)))   # -10 dB bump, absolute min 3 bins above
a = np.full(N_STEPS, 25)                             # background ~-20 dB (min-subtracted)
a[p - 2:p + 3] = [120, 150, 160, 150, 120]           # peak 160 ADC (150 above min)
a[t - 2:t + 3] = 57                                  # ~-10 dB vs peak
a[t + 3] = 10                                        # absolute min -> ~-180 dB bin
feat = extract_feature_single_sweep(np.array(FREQS), a)
assert feat['peak_freq_hz'] == FREQS[p], (feat['peak_freq_hz'], FREQS[p])

# g) S2S and coupling gate (Operating Manual Ch. 14.2, 14.6)
def fake_sweep(centroid_shift, peak):
    """A sweep whose raw-amplitude centroid moves with centroid_shift (Hz scale) and whose peak is `peak` ADC."""
    a = np.array([20 + (i % 7) * 10 for i in range(N_STEPS)], dtype=float)
    a[N_STEPS // 2 + int(centroid_shift)] = peak
    return pd.DataFrame({'freq_hz': FREQS, 'ampl_adc': a})
steady = [fake_sweep(0, 150) for _ in range(30)]
m = block_metrics(steady)
assert m['s2s_hz'] == 0 and m['gate'] == 'PASS' and m['n_sweeps'] == 30, m
jumpy = [fake_sweep(3 * (i % 2), 150) for i in range(30)]          # alternating centroid -> S2S > 0
assert block_metrics(jumpy)['s2s_hz'] > 0
weak = [fake_sweep(0, 90) for _ in range(30)]                      # mean peak below 110 ADC
assert block_metrics(weak)['gate'] == 'FAIL' and 'mean peak < 110 ADC' in block_metrics(weak)['gate_reasons']
fading = [fake_sweep(0, 200 - 4 * i) for i in range(30)]           # peak falls by > 15 %
assert any('peak drop' in r for r in block_metrics(fading)['gate_reasons'])
assert block_metrics(steady[:10])['gate'] == 'FAIL'                # fewer than 20 sweeps

# h) passive noise: median over the pauses of std(P rows)
rows = [[0, 0, 0, 0, 'META', '# READY']]
ms = 0
for pause in range(4):
    for i, f in enumerate(FREQS):
        ms += 80; rows.append([0, f, 100, ms, 'S', ''])
    for k in range(10):
        ms += 100; v = 508 + (k % 2) * 2 * (pause + 1)            # pause n alternates +-(n+1) around 509..
        rows.append([0, 0, v, ms, 'P', ''])
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "p.csv"
    with p.open('w', newline='') as fh:
        csv.writer(fh).writerows([HEADER] + rows)
    stds = [np.std([508 + (k % 2) * 2 * (n + 1) for k in range(10)], ddof=1) for n in range(4)]
    assert abs(passive_noise_adc(str(p)) - np.median(stds)) < 1e-9
    # t_from as used by 03: only the pauses after it count (pauses 3 and 4 here)
    t_mid = (rows[1 + 2 * (N_STEPS + 10)][3] - 80) / 1000.0     # start of the third sweep, t_sec axis
    assert abs(passive_noise_adc(str(p), t_from=t_mid) - np.median(stds[2:])) < 1e-9

# i) a flat sweep at the noise floor is left out of the S2S, as in extract_feature_single_sweep
flat = pd.DataFrame({'freq_hz': FREQS, 'ampl_adc': np.full(N_STEPS, 11.0)})
flat.loc[N_STEPS // 2, 'ampl_adc'] = 13.0                         # 2 ADC of range: below MIN_DYN_ADC
assert block_metrics(steady + [flat])['n_sweeps'] == len(steady)
assert np.isnan(extract_feature_single_sweep(flat['freq_hz'].to_numpy(), flat['ampl_adc'].to_numpy())['centroid_raw_hz'])

print("ok")