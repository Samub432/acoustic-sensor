# Copyright 2026 Samuele Bettiol and the iGEM 2026 Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""
ACOUSTIC SENSOR FILE 3
FEATURE EXTRACTION AND INTERPRETATION

Each sweep is a frequency response (peak-to-peak ADC amplitude at each swept frequency):
it is converted into a set of numerical features that are easier to interpret and track over time.

For each sweep, this features are computed:

FEATURES EXTRACTED PER SWEEP:
- centroid_hz       -> spectral centroid, indicates where most of the energy is concentrated
                       (dominated by the resonances of the system; the bubble-size reading of its
                        direction is a hypothesis pending physical calibration)
- peak_freq_hz      -> dominant frequency in the spectrum
- peak_ampl_db      -> raw peak-to-peak ADC of the dominant resonance in dB; coupling check
                       (~150 ADC ≈ 43.5 dB with good coupling), intra-session only
- rms_abs_db        -> absolute signal energy on raw ADC (root mean square); candidate indicator of
                       gas/biological activity, not calibrated, intra-session comparisons only
- high_freq_ratio   -> proportion of energy above a defined threshold (spectral shape descriptor;
                       the bubble-size/granulometry reading is still to be validated with a physical calibration)

SLOW TREND AND VARIABILITY:
- slow              -> moving average over SLOW_WINDOW_N = 10 sweeps (~100 s at the current ~10 s sweep period)
- centroid_sigma_hz -> centroid variability (rolling std) over the same window

HOW TO USE
#extract features from a CSV file produced by 01_data_from_sensor.py
    python 03_features.py --csv data/session.csv
# save the features to a new CSV file
    python 03_features.py --csv data/session.csv --out feature_table.csv
# save the plot as PNG instead of showing it
    python 03_features.py --csv data/session.csv --save
# combine both options: save features and plot
    python 03_features.py --csv data/session.csv --out feat.csv --save
# light/dark blocks: state from the file name (or --state), append to a summary CSV
# and print the bracketed light-dark comparison
    python 03_features.py --csv trials/06_elodea_2/light1_2.csv --summary e2.csv
"""

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import argparse
import sys
from datetime import datetime
from pathlib import Path
from acoustic_common import (HIGH_FREQ_THRESH, CENTROID_MIN_HALFSPAN_HZ, DB_MIN_HALFSPAN,
                             load_and_segment, extract_feature_single_sweep, set_centered_ylim)


#  CONFIGURATION
SLOW_WINDOW_N    = 10      # N sweeps for the "slow" moving average
# heuristic, set from trials 03–08: coupling-driven files |r| >= 0.68, all others <= 0.54; re-check on new data
QC_CORR_THRESH   = 0.6
QC_MIN_SWEEPS    = 20      # min valid sweeps for a meaningful QC correlation
SETTLE_S         = 180     # one file = one light block: the light switches at file start; the first minutes are a coupling/thermal transient


#
def extract_feature_session(sweeps: list) -> pd.DataFrame:
    """
    Extracts the features of every sweep and builds a time-indexed DataFrame.
    Adds a moving average (<col>_slow) for each feature and the
    rolling std of the centroid (centroid_sigma_hz).
    """

    rows = []
    for i, sweep in enumerate(sweeps):
        freq = sweep['freq_hz'].to_numpy()
        ampl = sweep['ampl_adc'].to_numpy()
        t    = sweep['t_sec'].mean()

        feat = extract_feature_single_sweep(freq, ampl)

        feat['t_sec']    = round(t, 2)
        feat['sweep_n']  = i
        rows.append(feat)

    df = pd.DataFrame(rows)

    #  TIME SCALE SEPARATION
    # Slow moving average (biological)
    numeric_features = [
        'centroid_hz', 'rms_abs_db', 'high_freq_ratio', 'peak_ampl_db'
    ]
    for col in numeric_features:
        if col in df.columns: #same operation for all the features
            # moving average over SLOW_WINDOW_N sweeps
            df[f'{col}_slow'] = (
                df[col].rolling(window=SLOW_WINDOW_N, center=True, min_periods=1).mean())

    # rolling std of the centroid = bubbling index (primary output #2)
    df['centroid_sigma_hz'] = (
        df['centroid_hz'].rolling(window=SLOW_WINDOW_N, center=True, min_periods=3).std())

    print(f"[FEATURE] Features extracted for {len(df)} sweeps")
    return df


#
def qc_coupling(df_feat: pd.DataFrame):
    """
    Coupling QC: if peak amplitude and centroid move together, the session is
    likely driven by the sensor coupling, not by biology.

    Returns:
        (status, r, n) with status 'OK', 'WARNING' or 'N/A'
    """
    n = int((df_feat['peak_ampl_db'].notna() & df_feat['centroid_hz'].notna()).sum())
    if n < QC_MIN_SWEEPS:
        return 'N/A', np.nan, n   # no corr on too few points (numpy warns on n <= 1)
    r = df_feat['peak_ampl_db'].corr(df_feat['centroid_hz'])
    return ('WARNING' if abs(r) >= QC_CORR_THRESH else 'OK'), r, n


def qc_text(qc) -> str:
    """One-line QC message, shared by the terminal summary and the dashboard banner."""
    status, r, n = qc
    if status == 'N/A':
        return f"QC         {status:<10}too few sweeps (n={n}, need {QC_MIN_SWEEPS})"
    text = f"QC         {status:<10}r(peak, centroid) = {r:+.2f}  (n={n})"
    if status == 'WARNING':
        text += " -> peak and centroid move together: likely coupling, not biology"
    return text


#
def plot_feature_dashboard(df_feat: pd.DataFrame, qc, title: str, settle_s: float):
    """
    Complete dashboard of the features over time.
    Layout: 3 rows x 2 columns, all panels share the time axis
    Row 1: centroid (raw + slow)   | centroid variability (centroid_sigma_hz)
    Row 2: RMS energy (raw + slow) | high freq ratio (raw + slow)
    Row 3: peak amplitude, coupling check (raw + slow), full width
    QC banner (qc = result of qc_coupling) under the title (file name + state).
    The first settle_s seconds (excluded from the statistics) are shaded grey.
    """
    t = df_feat['t_sec'].to_numpy()

    fig = plt.figure(figsize=(16, 8))
    gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.3)

    # palette
    C_RAW  = "#413e3e"   # grey: raw per-sweep value
    C_SLOW = '#185FA5'   # blue: slow moving average

    #  1. SPECTRAL CENTROID
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(t, df_feat['centroid_hz'], color=C_RAW, lw=0.8, alpha=0.5)
    ax1.plot(t, df_feat['centroid_hz_slow'], color=C_SLOW, lw=1.5)
    set_centered_ylim(ax1, df_feat['centroid_hz'], CENTROID_MIN_HALFSPAN_HZ)
    ax1.set_ylabel('Hz')
    ax1.set_title('Spectral centroid')
    ax1.grid(True, alpha=0.2)

    #  2. CENTROID VARIABILITY
    ax1b = fig.add_subplot(gs[0, 1], sharex=ax1)
    ax1b.plot(t, df_feat['centroid_sigma_hz'], color=C_SLOW, lw=1.5)
    ax1b.set_ylim(bottom=0)
    ax1b.set_ylabel('Hz')
    ax1b.set_title('Centroid variability (rolling sigma)')
    ax1b.grid(True, alpha=0.2)

    #  3. RMS ENERGY — absolute only (rms_abs_db, raw ADC counts)
    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
    ax2.plot(t, df_feat['rms_abs_db'], color=C_RAW, lw=0.8, alpha=0.5)
    ax2.plot(t, df_feat['rms_abs_db_slow'], color=C_SLOW, lw=1.5)
    set_centered_ylim(ax2, df_feat['rms_abs_db'], DB_MIN_HALFSPAN)
    ax2.set_ylabel('dB (absolute)')
    ax2.set_title('Acoustic RMS energy')
    ax2.grid(True, alpha=0.2)

    #  4. HIGH FREQ RATIO
    ax3 = fig.add_subplot(gs[1, 1], sharex=ax1)
    ax3.plot(t, df_feat['high_freq_ratio'], color=C_RAW, lw=0.8, alpha=0.5)
    ax3.plot(t, df_feat['high_freq_ratio_slow'], color=C_SLOW, lw=1.5)
    ax3.set_ylabel('ratio 0–1')
    ax3.set_title(f'High freq ratio (> {HIGH_FREQ_THRESH} Hz)\n'
                  'spectral shape descriptor — granulometry to be validated')
    ax3.set_ylim(0, 1)
    ax3.grid(True, alpha=0.2)

    #  5. PEAK AMPLITUDE - coupling check, not a biological feature
    ax4 = fig.add_subplot(gs[2, :], sharex=ax1)
    ax4.plot(t, df_feat['peak_ampl_db'], color=C_RAW, lw=0.8, alpha=0.5)
    ax4.plot(t, df_feat['peak_ampl_db_slow'], color=C_SLOW, lw=1.5)
    set_centered_ylim(ax4, df_feat['peak_ampl_db'], DB_MIN_HALFSPAN)
    ax4.set_ylabel('dB (ADC)')
    ax4.set_xlabel('Time (s)')
    ax4.set_title('Peak amplitude — coupling check (raw ADC)')
    ax4.grid(True, alpha=0.2)

    # x tick labels only on the bottom row
    for ax in (ax1, ax1b, ax2, ax3):
        ax.tick_params(labelbottom=False)

    # settling transient, excluded from the statistics
    if settle_s > 0:
        for ax in (ax1, ax1b, ax2, ax3, ax4):
            ax.axvspan(t[0], t[0] + settle_s, color='#DDDDDD', alpha=0.6, lw=0,
                       label='settling (excluded)' if ax is ax1 else None)
        ax1.legend(loc='upper right', fontsize=8)

    fig.suptitle(f'Acoustic feature dashboard — {title}', fontsize=12)
    qc_color = {'WARNING': '#C62828', 'OK': '#2E7D32'}.get(qc[0], '#757575')
    fig.text(0.5, 0.935, qc_text(qc), ha='center', fontsize=10, color=qc_color)
    return fig



def session_stats(df_feat: pd.DataFrame) -> dict:
    """Session statistics, computed only on the used sweeps (settling excluded)."""
    u = df_feat.loc[df_feat['used']]
    return {
        'centroid_hz':       u['centroid_hz'].mean(),
        'centroid_sigma_hz': u['centroid_sigma_hz'].median(),
        'rms_abs_db':        u['rms_abs_db'].mean(),
        'peak_ampl_db':      u['peak_ampl_db'].mean(),
        'high_freq_ratio':   u['high_freq_ratio'].mean(),
    }


def linear_trend_per_min(df: pd.DataFrame, col: str) -> float:
    """Slope of a linear regression of col vs time, per minute. NaN ignored."""
    v = df[['t_sec', col]].dropna()
    if len(v) < 2:
        return np.nan
    return np.polyfit(v['t_sec'], v[col], 1)[0] * 60


def print_summary(df_feat: pd.DataFrame, qc, stats: dict, file_name: str,
                  state: str, state_src: str, settle_s: float):
    """
    Prints a short numeric summary of the session to the terminal.
    qc = result of qc_coupling, printed right after FILE and SWEEPS (even with too few sweeps).
    """
    n_used = int(df_feat['used'].sum())
    print("\n" + "-" * 55)
    print("  SESSION SUMMARY")
    print("-" * 55)
    print(f"  {'FILE':<11}{file_name}   (state: {state}, from {state_src})")
    excl = f"first {settle_s:g} s excluded: settling" if settle_s > 0 else "no settling exclusion"
    print(f"  {'SWEEPS':<11}{n_used} used / {len(df_feat)}   ({excl})")
    print("  " + qc_text(qc))

    # too few sweeps to fit a trend
    if n_used < 5:
        print("-" * 55 + "\n")
        return

    u = df_feat.loc[df_feat['used']]
    print(f"  {'CENTROID':<11}{stats['centroid_hz']:.1f} Hz    "
          f"median rolling sigma {stats['centroid_sigma_hz']:.1f} Hz")
    print(f"  {'RMS':<11}{stats['rms_abs_db']:.2f} dB     PEAK {stats['peak_ampl_db']:.2f} dB     "
          f"HFR {stats['high_freq_ratio']:.3f}")
    # The centroid position/direction is dominated by the resonances of the SYSTEM
    # (piezo + wall + liquid): its trend is reported as a number, not read as bubble size.
    # RMS trend not calibrated, intra-session only.
    print(f"  {'TREND':<11}centroid {linear_trend_per_min(u, 'centroid_hz'):+.2f} Hz/min    "
          f"RMS {linear_trend_per_min(u, 'rms_abs_db'):+.3f} dB/min")
    print("-" * 55 + "\n")


SUMMARY_FEATURES = ['centroid_hz', 'centroid_sigma_hz', 'rms_abs_db', 'peak_ampl_db', 'high_freq_ratio']


def update_summary(summary_path: str, row: dict) -> pd.DataFrame:
    """Writes or replaces the row of this file (key = folder + file) in the summary CSV."""
    p = Path(summary_path)
    if p.exists():
        s = pd.read_csv(p)
        s = s.loc[~((s['folder'] == row['folder']) & (s['file'] == row['file']))]
        s = pd.concat([s, pd.DataFrame([row])], ignore_index=True)
    else:
        s = pd.DataFrame([row])
    s.to_csv(p, index=False)
    print(f"[SAVE] Summary: {summary_path} ({len(s)} rows)")
    return s


def print_light_dark_comparison(s: pd.DataFrame):
    """Bracketed LIGHT - DARK comparison over the blocks of the summary CSV."""
    blocks = s.loc[s['state'].isin(['LIGHT', 'DARK'])]
    bad = blocks.loc[blocks['qc_status'] != 'OK']
    if len(bad):
        print(f"  excluded by QC: {', '.join(bad['file'])}")
    blocks = blocks.loc[blocks['qc_status'] == 'OK']

    if s['folder'].nunique() > 1:
        print("  WARNING: blocks from different sessions — comparison skipped (intra-session rule)")
        return

    blocks = blocks.sort_values('start_time').reset_index(drop=True)
    # bracketed comparison: a linear drift across the session cancels out; a plain
    # light-vs-next-dark comparison would read the drift as a light effect
    d = []
    for i in range(1, len(blocks) - 1):
        if (blocks.loc[i, 'state'] == 'DARK' and blocks.loc[i - 1, 'state'] == 'LIGHT'
                and blocks.loc[i + 1, 'state'] == 'LIGHT'):
            light = (blocks.loc[i - 1, SUMMARY_FEATURES] + blocks.loc[i + 1, SUMMARY_FEATURES]) / 2
            d.append(light - blocks.loc[i, SUMMARY_FEATURES])

    if len(d) < 3:
        print("  not enough alternating blocks (need >= 3 dark blocks bracketed by light)")
        return

    d = pd.DataFrame(d).astype(float)
    print("\n  LIGHT - DARK (bracketed, per block)")
    print(f"  {'feature':<20}{'n':>3}{'mean d':>11}{'std d':>10}{'d > 0':>8}{'p':>8}")
    for col in SUMMARY_FEATURES:
        v = d[col].dropna()
        _, p = ttest_1samp(v, 0)
        print(f"  {col:<20}{len(v):>3}{v.mean():>+11.3f}{v.std():>10.3f}"
              f"{f'{(v > 0).sum()}/{len(v)}':>8}{p:>8.3f}")
    print("  p is approximate: neighbouring comparisons share light blocks\n")


#----------------------------------------
#     MAIN
#----------------------------------------
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Feature extraction for the acoustic bioreactor')
    parser.add_argument('--csv',    required=True,  help='CSV file produced by 01_data_from_sensor.py')
    parser.add_argument('--out',    default=None,   help='Save the feature table as CSV (optional)')
    parser.add_argument('--save',  action='store_true', help='Save the dashboard as PNG instead of showing it')
    parser.add_argument('--state', choices=['light', 'dark', 'none'], default=None,
                        help='Light state of the block (default: from the file name)')
    parser.add_argument('--settle', type=float, default=SETTLE_S,
                        help=f'Seconds excluded at file start for LIGHT/DARK blocks (default {SETTLE_S})')
    parser.add_argument('--summary', default=None,
                        help='Summary CSV: write/replace one row per file and print the light-dark comparison')
    args = parser.parse_args()

    # light state: from the file name unless --state is given
    csv_path = Path(args.csv)
    name = csv_path.name.lower()
    if args.state:
        state, state_src = args.state.upper(), '--state'
    else:
        state_src = 'file name'
        if name.startswith('light'):
            state = 'LIGHT'
        elif name.startswith(('night', 'dark')):
            state = 'DARK'
        else:
            state = 'NONE'
            # e.g. sample1_light2.csv: the name mentions a light state but does not start with it
            if any(k in name for k in ('light', 'dark', 'night')):
                print("[WARN] The state is NONE: did you forget --state light/dark?")
    settle_s = args.settle if state in ('LIGHT', 'DARK') else 0

    # load and segment
    sweeps = load_and_segment(args.csv)
    if not sweeps:
        print("[ERR] No sweeps found: wrong CSV file, or no sweep data in it.")
        sys.exit(1)

    # extract features, every sweep will be transformed in a feature vector and stored in a DataFrame
    df_feat = extract_feature_session(sweeps)

    # settling exclusion: slow curves and centroid_sigma_hz stay on all sweeps,
    # the statistics use only the used ones
    df_feat['used']  = df_feat['t_sec'] >= df_feat['t_sec'].iloc[0] + settle_s
    df_feat['state'] = state

    # coupling QC, computed once and shared by the summary and the dashboard
    qc    = qc_coupling(df_feat.loc[df_feat['used']])
    stats = session_stats(df_feat)

    # print the numeric session summary
    print_summary(df_feat, qc, stats, csv_path.name, state, state_src, settle_s)

    # save features CSV if requested
    if args.out:
        df_feat.to_csv(args.out, index=False)
        print(f"[SAVE] Features: {args.out}")

    # summary CSV (one row per file) + light-dark comparison
    if args.summary:
        used_idx = np.flatnonzero(df_feat['used'].to_numpy())
        t_start  = sweeps[used_idx[0]]['pc_timestamp'].iloc[0] if len(used_idx) else np.nan
        row = {
            'file':       csv_path.name,
            'folder':     csv_path.parent.name,
            'state':      state,
            'start_time': datetime.fromtimestamp(float(t_start)).isoformat(timespec='seconds')
                          if pd.notna(t_start) else '',
            'n_used':     len(used_idx),
            'n_excluded': len(df_feat) - len(used_idx),
            'qc_status':  qc[0],
            'qc_r':       round(qc[1], 4) if pd.notna(qc[1]) else np.nan,
            **{k: round(v, 4) for k, v in stats.items()},
        }
        print_light_dark_comparison(update_summary(args.summary, row))

    # plot dashboard in the graphical window (or save as PNG if requested)
    # panels: centroid, centroid variability, RMS, high frequency ratio, peak amplitude
    fig = plot_feature_dashboard(df_feat, qc, f'{csv_path.name} ({state})', settle_s)

    if args.save:
        out_png = csv_path.stem + '_features.png'
        # no bbox_inches='tight': keep a fixed canvas size (figsize x dpi),
        # independent of sweep count
        fig.savefig(out_png, dpi=150)
        print(f"[SAVE] Plot: {out_png}")
    else:
        plt.show()
