# Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""
ACOUSTIC SENSOR - COMMON CODE
Shared by 01-04: port detection, CSV loading/cleaning, per-sweep features,
S2S and coupling gate of a block, passive noise, plot axis helper.
"""

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


#  CONFIGURATION
SMOOTH_W         = 7       # window for Savitzky-Golay smoothing
SMOOTH_P         = 2       # polynomial degree
# measured useful band (Hz): all spectral features are computed only inside it.
# Bins above ~3200 Hz are noise floor (~10 ADC) that would bias centroid/RMS.
ANALYSIS_BAND    = (1000, 3200)
# threshold splitting the useful band into low (1-2 kHz) and high (2-3 kHz).
# 2000 Hz separates the dominant loaded resonance (~1550 Hz) from the secondary
# cluster (~2120+ Hz).
HIGH_FREQ_THRESH = 2000    # Hz - threshold for high_freq_ratio calculation
MIN_AMPL_DB      = -30     # dB below the sweep peak: weaker bins count as noise and are ignored
MIN_DYN_ADC      = 20      # min in-band ADC range for a live sweep: noise floor ~10 ADC, good-coupling peak ~150 ADC
N_STEPS          = 60      # points per sweep, must match the firmware
MIN_SWEEP_POINTS = 55      # min points for a complete sweep (tolerance for a few lost lines)
SWEEP_START_MAX_HZ = 1010  # a complete sweep starts at the bottom of the frequency range
# plot axes (shared by 03 and 04)
CENTROID_MIN_HALFSPAN_HZ = 20   # baseline centroid sigma measured 3-25 Hz depending on the session: a baseline must look flat, no autoscale on noise
DB_MIN_HALFSPAN  = 1.0     # clean-file peak noise 0.1-0.3 dB, bubble effect ~4 dB: noise must look flat
# coupling gate of one block (Operating Manual Ch. 14.6): set on Trial 5, pre-registered for every later session
GATE_R_MIN         = -0.85  # r(peak, raw centroid) below this: the centroid follows the coupling, not the bubbles
GATE_PEAK_DROP_MAX = 0.15   # peak change, mean of the last 3 sweeps vs the first 3: a larger loss inflates the S2S
GATE_PEAK_MIN_ADC  = 110    # mean peak of the block: below this the S2S is inflated by quantisation noise
GATE_MIN_SWEEPS    = 20     # fewer sweeps: correlation and S2S not meaningful
PASSIVE_MAX_ADC    = 2.0    # passive noise (TX off): provisional threshold, reported but not used to exclude blocks


#--------------------------
#       FUNCTION
#--------------------------
def find_port():
    """
    Search for the Arduino serial port automatically.
    Returns the port name, or None if no candidate was found.
    """
    import serial.tools.list_ports   # imported here: 02 and 03 work without pyserial
    ports = list(serial.tools.list_ports.comports())

    # first port whose description looks like an Arduino (CH340 = the chip of the clones)
    for p in ports:
        if any(kw in p.description for kw in ['Arduino', 'CH340', 'USB Serial', 'ttyUSB']):
            print(f"[AUTO] Port found: {p.device} - {p.description}")
            return p.device

    # nothing matched: list what is there, so the user can pick one with --port
    print("[ERR] No Arduino found automatically. Available ports:")
    for p in ports:
        print(f"  {p.device:20s} {p.description}")
    return None


def _load_clean(csv_path: str):
    """
    Shared by load_and_segment and passive_noise_adc, so both use the same rows and the same time axis.
    Keeps only what follows the last "# READY", drops META rows, converts to numbers and adds t_sec.
    Returns (df, n_runs, n_stale).
    """
    df_raw = pd.read_csv(csv_path)

    # 01 opens the CSV in append mode, so one file can hold several runs; Arduino resets
    # at each run and arduino_ms restarts from ~0. Keep only what follows the LAST "# READY"
    # (this also drops stale serial-buffer lines). No "# READY" (--split files 2+) = keep all.
    # "# READY" is printed by every firmware version (the boot header changed from
    # "# BIOREATTORE ACUSTICO" to "# ACOUSTIC BIOREACTOR", so it can't be used)
    header_pos = np.flatnonzero(
        df_raw['raw_line'].astype(str).str.startswith('# READY').to_numpy())
    n_stale  = int(header_pos[-1]) if len(header_pos) else 0
    df_raw   = df_raw.iloc[n_stale:]

    df_raw = df_raw[df_raw['mode'] != 'META'].copy()

    for col in ['freq_hz', 'ampl_adc', 'arduino_ms']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce')

    df_raw.dropna(subset=['freq_hz', 'ampl_adc'], inplace=True)

    t0 = df_raw['arduino_ms'].min()
    df_raw['t_sec'] = (df_raw['arduino_ms'] - t0) / 1000.0
    return df_raw, len(header_pos), n_stale


def load_and_segment(csv_path: str):
    """
    Loads a CSV produced by 01, drops stale serial-buffer lines and
    incomplete sweeps, returns the list of complete sweeps (one DataFrame each).
    """
    df_raw, n_runs, n_stale = _load_clean(csv_path)
    if n_runs > 1:
        print(f"[LOAD] {n_runs} runs in the file, keeping only the last one")

    sweep_df   = df_raw[df_raw['mode'] == 'S'].copy()
    if sweep_df.empty:
        print("[WARN] No line in 'S' (sweep) mode was found")
        return []
    # a new sweep starts where the frequency falls back to the bottom of the range
    freq_diff  = sweep_df['freq_hz'].diff()
    breaks     = sweep_df.index[freq_diff < -sweep_df['freq_hz'] * 0.5].tolist()
    breaks     = [sweep_df.index[0]] + breaks + [sweep_df.index[-1] + 1]

    sweeps = []
    n_incomplete = 0
    for i in range(len(breaks) - 1):
        chunk = sweep_df.loc[breaks[i]:breaks[i+1]-1]
        # keep only complete sweeps (N_STEPS points per sweep, starting at the bottom of the range)
        if MIN_SWEEP_POINTS <= len(chunk) <= N_STEPS and chunk['freq_hz'].iloc[0] <= SWEEP_START_MAX_HZ:
            sweeps.append(chunk.reset_index(drop=True))
        else:
            n_incomplete += 1

    if n_stale or n_incomplete:
        print(f"[LOAD] {n_stale} lines before the last reset dropped, {n_incomplete} incomplete sweeps dropped")

    return sweeps


def extract_feature_single_sweep(freq: np.ndarray, ampl_raw: np.ndarray) -> dict:
    """
    Extract features from a single sweep of the acoustic signal.

    Args:
        freq:     array of frequencies in Hz
        ampl_raw: array of raw ADC amplitude (0-1023)

    Returns:
        dict with the features of the sweep (all NaN for a flat or empty sweep):
        - centroid_hz     (normalized spectrum, bins below MIN_AMPL_DB ignored)
        - centroid_raw_hz (raw ADC weights, the one used for the S2S)
        - peak_freq_hz    (max of the smoothed, normalized spectrum)
        - peak_ampl_db    (raw peak ADC in dB, coupling check; max of the raw ADC,
                           so it can fall on a different bin than peak_freq_hz)
        - rms_abs_db      (absolute energy on raw ADC; candidate activity indicator, not calibrated)
        - high_freq_ratio
    """
    nan_feat = {k: np.nan for k in [
        'centroid_hz', 'centroid_raw_hz', 'peak_freq_hz', 'peak_ampl_db',
        'rms_abs_db', 'high_freq_ratio'
    ]}

    # PREPROCESSING
    # 0. restrict to the measured useful band BEFORE any feature is computed,
    #    so noise bins above ANALYSIS_BAND never enter centroid/RMS/hfr
    band = (freq >= ANALYSIS_BAND[0]) & (freq <= ANALYSIS_BAND[1])
    if band.sum() < 3:
        return nan_feat
    freq     = freq[band]
    ampl_raw = ampl_raw[band]
    # flat sweep = dead sensor / no coupling: the centroid would just be the band mean
    if ampl_raw.max() - ampl_raw.min() < MIN_DYN_ADC:
        return nan_feat

    #  1. normalization: minimum at 0, maximum at 1, then relative dB
    ampl = ampl_raw.astype(float)
    ampl -= ampl.min()
    ampl_lin = np.where(ampl == 0, 1e-9, ampl)
    ampl_lin = ampl_lin / ampl_lin.max()
    ampl_db  = 20 * np.log10(ampl_lin + 1e-9)

    # 2. bins more than 30 dB below the peak are noise
    valid = ampl_db > MIN_AMPL_DB

    # 3. fewer than 3 bins above the noise: nothing to measure
    if valid.sum() < 3:
        return nan_feat

    # SMOOTHING AND FEATURES
    # smoothing gives a more stable peak (Savitzky-Golay needs more points than its window)
    if len(ampl_db) > SMOOTH_W:
        # floor before smoothing: the -180 dB min bin would ring into bins ±3 away
        ampl_smooth = np.asarray(savgol_filter(np.maximum(ampl_db, MIN_AMPL_DB), SMOOTH_W, SMOOTH_P))
    else:
        ampl_smooth = ampl_db

    #  FEATURE 1: spectral centroid
    # weight = linear amplitude (not dB)
    weight = ampl_lin[valid]
    freq_val = freq[valid]
    centroid_hz = np.dot(freq_val, weight) / (weight.sum() + 1e-9)

    #  FEATURE 2: main peak
    idx_peak    = np.argmax(ampl_smooth)
    peak_freq   = freq[idx_peak]
    # raw peak-to-peak ADC of the dominant resonance in dB; coupling check
    # (~150 ADC ≈ 43.5 dB with good coupling), intra-session only
    peak_ampl   = 20 * np.log10(ampl_raw.astype(float).max() + 1e-9)

    #  FEATURE 3: absolute RMS on RAW ADC (band-limited, NOT normalized)
    # Computed on ampl_raw (0-1023 counts) inside ANALYSIS_BAND, before any
    # normalization, so it follows the real acoustic energy. Candidate indicator
    # of gas/biological activity: not calibrated, compare only within a session.
    rms_abs_db = 10 * np.log10(np.mean(ampl_raw.astype(float) ** 2) + 1e-9)

    #  FEATURE 4: high_freq_ratio
    # Spectral shape descriptor: how much energy is in the high frequencies vs total.
    # The granulometric reading (high = small bubbles / low = large bubbles) is a
    # working hypothesis, to be validated with a physical calibration.
    # same 'valid' mask as the centroid: noise bins below MIN_AMPL_DB are ignored
    mask_high = valid & (freq > HIGH_FREQ_THRESH)
    mask_low  = valid & (freq <= HIGH_FREQ_THRESH)
    e_high = ampl_lin[mask_high].sum() if mask_high.any() else 0
    e_low  = ampl_lin[mask_low].sum()  if mask_low.any()  else 1e-9
    high_freq_ratio = e_high / (e_high + e_low + 1e-9)

    # centroid weighted by the RAW in-band ADC amplitude (no normalisation, no threshold):
    # the centroid used for the S2S (Operating Manual Ch. 14.2); centroid_hz gives S2S values 2-4x higher
    centroid_raw_hz = sweep_raw(freq, ampl_raw)[0]

    return {
        'centroid_hz':        round(centroid_hz,  1),
        'centroid_raw_hz':    round(centroid_raw_hz, 1),
        'peak_freq_hz':       round(float(peak_freq), 1),
        'peak_ampl_db':       round(float(peak_ampl), 2),
        'rms_abs_db':         round(rms_abs_db, 2),
        'high_freq_ratio':    round(high_freq_ratio, 4),
    }


def sweep_raw(freq: np.ndarray, ampl_raw: np.ndarray):
    """Raw-amplitude centroid (Hz) and peak (ADC) of one sweep inside ANALYSIS_BAND."""
    f = np.asarray(freq, dtype=float)
    a = np.asarray(ampl_raw, dtype=float)
    b = (f >= ANALYSIS_BAND[0]) & (f <= ANALYSIS_BAND[1])
    if not b.any() or np.ptp(a[b]) < MIN_DYN_ADC:
        return np.nan, np.nan      # flat sweep (dead sensor / no coupling): same guard as extract_feature_single_sweep
    return np.dot(f[b], a[b]) / a[b].sum(), a[b].max()


def block_metrics(sweeps: list) -> dict:
    """
    S2S and coupling-gate quantities of one block (Operating Manual Ch. 14.2 and 14.6).
    sweeps: the sweeps of the block as returned by load_and_segment (settling already removed).
    S2S = std of the differences between consecutive raw-amplitude centroids.
    """
    cp = np.array([sweep_raw(s['freq_hz'].to_numpy(), s['ampl_adc'].to_numpy()) for s in sweeps]).reshape(-1, 2)
    cp = cp[np.isfinite(cp).all(axis=1)]          # flat sweeps (in-band range < MIN_DYN_ADC) are left out
    out = {'n_sweeps': len(cp), 's2s_hz': np.nan, 'peak_mean_adc': np.nan,
           'peak_change_pct': np.nan, 'r_peak_centroid': np.nan}
    if len(cp) >= 3:
        c, p = cp.T
        out['s2s_hz']          = float(np.std(np.diff(c), ddof=1))
        out['peak_mean_adc']   = float(p.mean())
        out['peak_change_pct'] = float(100 * (p[-3:].mean() - p[:3].mean()) / p[:3].mean())
        if np.std(c) > 0 and np.std(p) > 0:   # constant centroid or peak: no drift to correlate, r stays NaN
            out['r_peak_centroid'] = float(np.corrcoef(p, c)[0, 1])

    reasons = []
    if out['n_sweeps'] == 0:
        reasons.append("no sweep with signal in band")
    elif out['n_sweeps'] < GATE_MIN_SWEEPS:
        reasons.append(f"< {GATE_MIN_SWEEPS} sweeps")
    if out['r_peak_centroid'] < GATE_R_MIN:
        reasons.append(f"r(peak, raw centroid) < {GATE_R_MIN}")
    if out['peak_change_pct'] < -100 * GATE_PEAK_DROP_MAX:
        reasons.append(f"peak drop > {100 * GATE_PEAK_DROP_MAX:.0f} %")
    if out['peak_mean_adc'] < GATE_PEAK_MIN_ADC:
        reasons.append(f"mean peak < {GATE_PEAK_MIN_ADC} ADC")
    out['gate'] = 'FAIL' if reasons else 'PASS'
    out['gate_reasons'] = reasons
    return out


def passive_noise_adc(csv_path: str, t_from: float = 0.0) -> float:
    """
    Passive noise of a block (Operating Manual Ch. 14.6): median over the pauses between sweeps
    of std(ampl_adc of the P rows of the pause), in ADC. Only P rows with t_sec >= t_from
    (same time axis as load_and_segment). NaN when the file has no P rows.
    """
    df, _, _ = _load_clean(csv_path)
    if df.empty:
        return np.nan
    pause = (df['mode'] != 'P').cumsum()          # a new pause starts after every sweep row
    sel = (df['mode'] == 'P') & (df['t_sec'] >= t_from)
    stds = df.loc[sel, 'ampl_adc'].groupby(pause[sel]).agg(lambda v: v.std(ddof=1) if len(v) >= 3 else np.nan).dropna()
    return float(np.median(stds)) if len(stds) else np.nan


def set_centered_ylim(ax, series: pd.Series, min_half: float):
    """ylim centered on the midpoint (max+min)/2, half-span = max(0.6*(max-min), min_half). NaN ignored."""
    v = series.dropna()
    if len(v):
        mid  = (v.max() + v.min()) / 2
        half = max(0.6 * (v.max() - v.min()), min_half)
        ax.set_ylim(mid - half, mid + half)