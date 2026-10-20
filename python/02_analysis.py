# Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""
ACOUSTIC SENSOR FILE 2
ANALYSIS OF THE SWEEP SPECTRA

Read the CSV file produced by the 01 file and produce:
- Frequency response curve (single sweep)
- temporal evolution of the response (waterfall plot)
- comparison against a separately recorded baseline

#HOW TO USE
# Analysis of the CSV file produced by 01_data_from_sensor.py
    python 02_analysis.py --csv data/session.csv
# show only the last sweep
    python 02_analysis.py --csv data/session.csv --show sweep
# show only the waterfall plot
    python 02_analysis.py --csv data/session.csv --show waterfall
# save the plots as PNG instead of showing them
# (output: plot_session_last_sweep.png, plot_session_waterfall.png)
    python 02_analysis.py --csv data/session.csv --save
# compare the session against a baseline recorded separately (see below)
    python 02_analysis.py --csv data/session.csv --baseline data/baseline.csv --show baseline

To record a baseline run on demand (water only),
run the logger separately and point it to its own file:
    python 01_data_from_sensor.py --port COM3 --out data/baseline.csv --duration 120

"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter
import argparse
import sys
from pathlib import Path
from acoustic_common import ANALYSIS_BAND, load_and_segment

#CONFIGURATION------------------------------------------------------------------------------
SMOOTH_WINDOW    = 7       # points in the Savitzky-Golay window
SMOOTH_POLY      = 2       # degree of the smoothing polynomial
WATERFALL_MAX_N  = 80      # the waterfall shows only the last 80 sweeps, more become unreadable
NOISE_FLOOR_DB   = -40     # relative dB floor: below this level it is only noise

#--------------------------------------------------------------------------------------------
#       FUNCTION
#--------------------------------------------------------------------------------------------
def normalize_amplitude(ampl: np.ndarray) -> np.ndarray:
    """
    Normalize amplitude ADC:
    1. Subtracts minimum (removes DC offset)
    2. Divide by the maximum (scale 0-1)
    3. Converts to relative dB, floored at NOISE_FLOOR_DB
       (the min bin would otherwise sit at ~-160 dB)
    """
    ampl = ampl.astype(float)
    ampl -= ampl.min() # remove offset
    ampl = np.where(ampl == 0, 1e-6, ampl)  # avoid log(0)
    ampl_norm = ampl / ampl.max() # normalization between 0 and 1
    ampl_db = 20 * np.log10(ampl_norm + 1e-9)  # relative measurement in dB 
    return np.maximum(ampl_db, NOISE_FLOOR_DB)



def plot_single_sweep(sweep: pd.DataFrame, title: str = "Frequency response"):
    """
    Plots the response curve of a single sweep.
    Show: raw curve, smoothed curve, highlighted peaks.
    """
    freq  = sweep['freq_hz'].to_numpy()
    ampl  = normalize_amplitude(sweep['ampl_adc'].to_numpy())  # already floored at NOISE_FLOOR_DB

    if len(ampl) > SMOOTH_WINDOW:
        ampl_smooth = np.asarray(savgol_filter(ampl, SMOOTH_WINDOW, SMOOTH_POLY))
    else:
        ampl_smooth = ampl

    fig, ax = plt.subplots(figsize=(10, 4))

    # raw curve in light grey behind, smoothed curve on top
    ax.plot(freq, ampl, color='#aaaaaa', linewidth=0.8,
            alpha=0.5, label='Raw ADC')
    ax.plot(freq, ampl_smooth, color='#185FA5', linewidth=1.8,
            label='Smoothed')

    # main peak, taken on the smoothed curve
    idx_peak = np.argmax(ampl_smooth)
    ax.scatter(freq[idx_peak], ampl_smooth[idx_peak],
               color='#D85A30', s=60, zorder=5,
               label=f'Peak: {freq[idx_peak]:.0f} Hz')

    ax.set_xlabel('Frequency (Hz)')
    ax.set_ylabel('Amplitude (relative dB)')
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.set_xscale('log')  # sweep points are log-spaced
    # view limited to the swept band (1000-3500 Hz); above there is only empty noise
    ax.set_xlim(ANALYSIS_BAND[0], 3500)
    ax.grid(True, alpha=0.3, which='both')

    t_sweep = sweep['t_sec'].mean()
    ax.text(0.02, 0.97, f't = {t_sweep:.1f}s',
            transform=ax.transAxes, fontsize=9,
            verticalalignment='top', color='gray')

    plt.tight_layout()
    return fig



def plot_waterfall(sweeps: list):
    """
    Waterfall plot: each line = one sweep, X-axis = frequency, color = amplitude.
    Allows us to see the temporal evolution of the acoustic response:
    appearance/disappearance of peaks and gradual color shifts over time.
    """
    if len(sweeps) == 0:
        print("[WARN] No sweep for waterfall")
        return None

    sweeps_plot = sweeps[-WATERFALL_MAX_N:]

    # matrix: one row per sweep (time), one column per frequency,
    # all sweeps on the frequency grid of the first one
    n_sweeps = len(sweeps_plot)

    freq_ref = sweeps_plot[0]['freq_hz'].to_numpy()

    matrix = np.zeros((n_sweeps, len(freq_ref)))
    times  = []

    for i, sweep in enumerate(sweeps_plot):
        freq_s = sweep['freq_hz'].to_numpy()
        ampl_s = normalize_amplitude(sweep['ampl_adc'].to_numpy())

        # a sweep with a few lost lines is interpolated onto the reference grid
        if len(freq_s) == len(freq_ref):
            matrix[i, :] = ampl_s
        else:
            matrix[i, :] = np.interp(freq_ref, freq_s, ampl_s)

        times.append(sweep['t_sec'].mean())

    matrix = np.clip(matrix, NOISE_FLOOR_DB, 0)

    fig, ax = plt.subplots(figsize=(10, 5))

    # pcolormesh, not imshow: sweep frequencies are log-spaced, imshow would place columns linearly
    im = ax.pcolormesh(freq_ref, times, matrix, cmap='viridis',  # yellow = high, blue/purple = low
                       shading='nearest')
    ax.invert_yaxis()  # time increasing downwards
    plt.colorbar(im, ax=ax, label='relative dB')
    ax.set_xlabel('Frequency (Hz)')
    ax.set_ylabel('Time (s)')
    ax.set_title('Waterfall - evolution of the acoustic response')
    ax.set_xscale('log')
    ax.set_xlim(ANALYSIS_BAND[0], 3500)  # swept band only, not the empty 3.5-20 kHz

    plt.tight_layout()
    return fig


def plot_baseline_comparison(sweeps: list, baseline_sweeps: list):
    """
    Compare a separately recorded baseline (e.g. water-only run, captured
    on demand and saved to its own CSV) against the sweeps of the current
    session. Highlights the spectral differences from the baseline.
    """
    if not baseline_sweeps:
        print("[WARN] Empty baseline, comparison skipped")
        return None

    session_sweeps = sweeps

    # mean and spread of the baseline sweeps, on the grid of its first sweep
    freq_ref = baseline_sweeps[0]['freq_hz'].to_numpy()
    baseline_mat = np.array([
        np.interp(
            freq_ref,
            s['freq_hz'].to_numpy(),
            normalize_amplitude(s['ampl_adc'].to_numpy())
        )
        for s in baseline_sweeps
    ])
    baseline_mean = baseline_mat.mean(axis=0)
    baseline_std  = baseline_mat.std(axis=0)

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

    # BASELINE
    ax = axes[0]
    ax.plot(freq_ref, baseline_mean, color='#185FA5',
            linewidth=1.5, label='Baseline (water)')
    ax.fill_between(freq_ref,
                    baseline_mean - baseline_std,
                    baseline_mean + baseline_std,
                    alpha=0.2, color='#185FA5', label='±1 std')
    ax.set_ylabel('Amplitude (dB)')
    ax.set_title('Baseline vs session')
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # DELTA SESSION - BASELINE
    ax2 = axes[1]
    cmap = plt.get_cmap('viridis')  # color = time: purple = first sweeps, yellow = last (colorblind-safe)
    colors = cmap(np.linspace(0.1, 0.9, len(session_sweeps)))

    for i, sweep in enumerate(session_sweeps):
        ampl_s = np.interp(
            freq_ref,
            sweep['freq_hz'].to_numpy(),
            normalize_amplitude(sweep['ampl_adc'].to_numpy())
        )
        delta = ampl_s - baseline_mean
        t_label = sweep['t_sec'].mean()
        ax2.plot(freq_ref, delta, color=colors[i],
                 linewidth=0.8, alpha=0.7,
                 label=f't={t_label:.0f}s' if i % 10 == 0 else None)

    ax2.axhline(0, color='black', linewidth=0.8, linestyle='--', alpha=0.4)
    ax2.set_xlabel('Frequency (Hz)')
    ax2.set_ylabel('Delta dB compared to baseline')
    ax2.set_title('Difference from baseline (positive = more energy than water)')
    ax2.set_xscale('log')
    ax2.set_xlim(ANALYSIS_BAND[0], 3500)  # sharex -> also limits the baseline panel above
    ax2.grid(True, alpha=0.3)

    handles, labels = ax2.get_legend_handles_labels()
    if handles:
        ax2.legend(fontsize=7, ncol=3)

    plt.tight_layout()
    return fig


def print_statistics(sweeps: list):
    """Prints a statistical summary of the session"""
    if not sweeps:
        return

    print("\n" + "-" * 50)
    print("  SESSION STATISTICS")
    print("-" * 50)
    print(f"  Total sweeps:    {len(sweeps)}")
    print(f"  Total time:      {sweeps[-1]['t_sec'].max():.1f} s")
    print("-" * 50 + "\n")


#------------------------------------------------------------
#    MAIN
#------------------------------------------------------------
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Acoustic bioreactor spectra analysis')
    parser.add_argument('--csv',  required=True, help='CSV file produced by 01_data_from_sensor.py')
    parser.add_argument('--baseline', default=None,
                        help='CSV of a separately recorded baseline run (e.g. water-only), '
                             'to compare against --csv. Valid only if the baseline was recorded '
                             'without remounting the piezos (otherwise inter-session drift dominates)')
    parser.add_argument('--show', default='all',
                        choices=['all', 'sweep', 'waterfall', 'baseline'],
                        help='Which plot to show')
    parser.add_argument('--save', action='store_true',
                        help='Save plots as PNGs instead of displaying them')
    args = parser.parse_args()
    if args.show == 'baseline' and not args.baseline:
        parser.error("--show baseline requires --baseline <file.csv>")  # prints usage, exits with code 2

    sweeps = load_and_segment(args.csv)

    if not sweeps:
        print("[ERR] No sweeps found in the file. Check that Arduino was in sweep mode.")
        sys.exit(1)

    print_statistics(sweeps)

    figs = []

    #  SINGLE SWEEP PLOT (the most recent one)
    if args.show in ('all', 'sweep'):
        fig = plot_single_sweep(
            sweeps[-1],
            title=f"frequency response - sweep {len(sweeps)} "
                   f"(t={sweeps[-1]['t_sec'].mean():.1f}s)"
        )
        figs.append(('last_sweep', fig))

    # WATERFALL
    if args.show in ('all', 'waterfall') and len(sweeps) > 2:
        fig = plot_waterfall(sweeps)
        if fig:
            figs.append(('waterfall', fig))

    #  BASELINE COMPARISON
    if args.show in ('all', 'baseline') and args.baseline:
        baseline_sweeps = load_and_segment(args.baseline)
        if baseline_sweeps:
            print(f"[BASELINE] {len(baseline_sweeps)} sweeps, "
                  f"{baseline_sweeps[-1]['t_sec'].max():.1f} s")
        fig = plot_baseline_comparison(sweeps, baseline_sweeps)
        if fig:
            figs.append(('baseline_comparison', fig))

    if args.save:
        base = Path(args.csv).stem
        for name, fig in figs:
            out = f"plot_{base}_{name}.png"
            fig.savefig(out, dpi=150, bbox_inches='tight')
            print(f"[SAVE] {out}")
    else:
        plt.show()
