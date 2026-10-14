"""Self-check for acoustic_common loading/features (run: python test_common.py)."""
import tempfile, csv, io, contextlib, math
from pathlib import Path
import numpy as np
from acoustic_common import load_and_segment, extract_feature_single_sweep

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

print("ok")
