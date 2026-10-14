# Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""
ACOUSTIC SENSOR - COMMON CODE
Shared by 01-04: port detection, CSV loading/cleaning, per-sweep features, plot axis helper.
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
# Was 8000 Hz, but there is no signal above 3 kHz here so it was always ~0.
HIGH_FREQ_THRESH = 2000    # Hz - threshold for high_freq_ratio calculation
MIN_AMPL_DB      = -30     # noise threshold (below = ignored)
MIN_DYN_ADC      = 20      # min in-band ADC range for a live sweep: noise floor ~10 ADC, good-coupling peak ~150 ADC
N_STEPS          = 60      # points per sweep, must match the firmware
MIN_SWEEP_POINTS = 55      # min points for a complete sweep (tolerance for a few lost lines)
SWEEP_START_MAX_HZ = 1010  # a complete sweep starts at the bottom of the frequency range
# plot axes (shared by 03 and 04)
CENTROID_MIN_HALFSPAN_HZ = 20   # baseline centroid sigma measured 3–25 Hz depending on the session: a baseline must look flat, no autoscale on noise
DB_MIN_HALFSPAN  = 1.0     # clean-file peak noise 0.1–0.3 dB, bubble effect ~4 dB: noise must look flat


#--------------------------
#       FUNCTION
#--------------------------
def find_port():
    """
    Search for the Arduino serial port automatically.
    Returns the port name, or None if no candidate was found.
    """
    #Import all the ports that Arduino has and put all of them in a list
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())

    for p in ports:
        # If i find in the description of the port one of this name, then print it
        if any(kw in p.description for kw in ['Arduino', 'CH340', 'USB Serial', 'ttyUSB']):
            print(f"[AUTO] Port found: {p.device} — {p.description}")
            return p.device

    # If there are no ports, this message is shown
    print("[ERR] No Arduino found automatically. Available ports:")
    for p in ports:
        print(f"  {p.device:20s} {p.description}")
    return None


#
def load_and_segment(csv_path: str):
    """
    Loads a CSV produced by 01, drops stale serial-buffer lines and
    incomplete sweeps, returns the list of complete sweeps (one DataFrame each).
    """
    df_raw = pd.read_csv(csv_path)

    # 01 opens the CSV in append mode, so one file can hold several runs; Arduino resets
    # at each run and arduino_ms restarts from ~0. Keep only what follows the LAST "# READY"
    # (this also drops stale serial-buffer lines). No "# READY" (--split files 2+) = keep all.
    # "# READY" is printed by every firmware version (the boot header changed from
    # "# BIOREATTORE ACUSTICO" to "# ACOUSTIC BIOREACTOR", so it can't be used)
    header_pos = np.flatnonzero(
        df_raw['raw_line'].astype(str).str.startswith('# READY').to_numpy())
    if len(header_pos) > 1:
        print(f"[LOAD] {len(header_pos)} runs in the file, keeping only the last one")
    n_stale  = int(header_pos[-1]) if len(header_pos) else 0
    df_raw   = df_raw.iloc[n_stale:]

    df_raw = df_raw[df_raw['mode'] != 'META'].copy()

    for col in ['freq_hz', 'ampl_adc', 'arduino_ms']:
        df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce')

    df_raw.dropna(subset=['freq_hz', 'ampl_adc'], inplace=True)

    t0 = df_raw['arduino_ms'].min()
    df_raw['t_sec'] = (df_raw['arduino_ms'] - t0) / 1000.0

    sweep_df   = df_raw[df_raw['mode'] == 'S'].copy()
    if sweep_df.empty:
        print("[WARN] No line in 'S' (sweep) mode was found")
        return []
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


#
def extract_feature_single_sweep(freq: np.ndarray, ampl_raw: np.ndarray) -> dict:
    """
    Extract features from a single sweep of the acoustic signal.

    Args:
        freq:     array of frequencies in Hz
        ampl_raw: array of raw ADC amplitude (0-1023)

    Returns:
        dict with all the features extracted from the sweep, including:
        - centroid_hz
        - peak_freq_hz    (max of the smoothed, normalized spectrum)
        - peak_ampl_db    (raw peak ADC in dB, coupling check; max of the raw ADC,
                           so it can fall on a different bin than peak_freq_hz)
        - rms_abs_db      (absolute energy on raw ADC, biological activity)
        - high_freq_ratio
    """
    # NaN result reused by the early guards below
    nan_feat = {k: np.nan for k in [
        'centroid_hz', 'peak_freq_hz', 'peak_ampl_db',
        'rms_abs_db', 'high_freq_ratio'
    ]}

    # FIRST PART OF THE FUNCTION: FIRST PREPROCESSING OF THE RAW DATA
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

    #  1. normalization
    ampl = ampl_raw.astype(float)
    ampl -= ampl.min()
    ampl_lin = np.where(ampl == 0, 1e-9, ampl)
    ampl_lin = ampl_lin / ampl_lin.max()
    ampl_db  = 20 * np.log10(ampl_lin + 1e-9)

    # 2. ignore frequency under a certain threshold (for noise) (boolean)
    valid = ampl_db > MIN_AMPL_DB
    
    # 3. if the sweep is completely in the noise and there are too few valid point (under three valid points), return NaN for all features
    if valid.sum() < 3:
        return nan_feat

    # SECOND PART OF THE FUNCTION: SMOOTHING AND FEATURE EXTRACTION
    # smoothing for a more stable peak (only if there are enough points for the Savitzky-Golay filter)
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
    # normalization -> reflects the real acoustic energy = absolute activity
    # indicator (amount of gas / biological activity). This is the one to track
    # over a session.
    rms_abs_db = 10 * np.log10(np.mean(ampl_raw.astype(float) ** 2) + 1e-9)

    #  FEATURE 4: high_freq_ratio
    # Spectral shape descriptor: how much energy is in the high frequencies vs total.
    # The granulometric reading (high = small bubbles / low = large bubbles) is a
    # working hypothesis, to be validated with a physical calibration.
    # HIGH_FREQ_THRESH = 2000 Hz separates the dominant loaded resonance (~1550 Hz)
    # from the secondary cluster (~2120+ Hz).
    # same 'valid' mask as the centroid: noise bins below MIN_AMPL_DB are ignored
    mask_high = valid & (freq > HIGH_FREQ_THRESH)
    mask_low  = valid & (freq <= HIGH_FREQ_THRESH)
    e_high = ampl_lin[mask_high].sum() if mask_high.any() else 0
    e_low  = ampl_lin[mask_low].sum()  if mask_low.any()  else 1e-9
    high_freq_ratio = e_high / (e_high + e_low + 1e-9)

    return {
        'centroid_hz':        round(centroid_hz,  1),
        'peak_freq_hz':       round(float(peak_freq), 1),
        'peak_ampl_db':       round(float(peak_ampl), 2),
        'rms_abs_db':         round(rms_abs_db, 2),
        'high_freq_ratio':    round(high_freq_ratio, 4),
    }


#
def set_centered_ylim(ax, series: pd.Series, min_half: float):
    """ylim centered on the midpoint (max+min)/2, half-span = max(0.6*(max-min), min_half). NaN ignored."""
    v = series.dropna()
    if len(v):
        mid  = (v.max() + v.min()) / 2
        half = max(0.6 * (v.max() - v.min()), min_half)
        ax.set_ylim(mid - half, mid + half)
