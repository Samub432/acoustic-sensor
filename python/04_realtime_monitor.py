"""

 ACOUSTIC BIOREACTOR — Step 4: Real-time live monitor


Matplotlib window updated in real time while Arduino
transmits data. Does not require an intermediate CSV file.

Shows in real time:
  - current spectrum (last sweep)
  - centroid over time (last N sweeps)
  - RMS over time

Features computed by acoustic_common, identical to 03.

USAGE:
# Auto-detect port
    python 04_realtime_monitor.py
# Specify port
    python 04_realtime_monitor.py --port COM3
# Linux
    python 04_realtime_monitor.py --port /dev/ttyUSB0

DEPENDENCIES:
    pip install pyserial numpy pandas scipy matplotlib

"""

import serial
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import time
import threading
import argparse
import sys
from acoustic_common import (find_port, extract_feature_single_sweep, set_centered_ylim,
                             ANALYSIS_BAND, N_STEPS, MIN_SWEEP_POINTS, SWEEP_START_MAX_HZ,
                             CENTROID_MIN_HALFSPAN_HZ, DB_MIN_HALFSPAN)


#  CONFIGURATION 
BAUD_RATE       = 115200
BUFFER_SWEEP_N  = 40     # how many sweeps to keep in memory (rolling)
UPDATE_MS       = 200    # ms between plot updates

# 
class DataBuffer:
    """
    Thread-safe buffer for incoming data from Arduino.
    The serial thread writes, the plot thread reads.
    """

    def __init__(self, max_sweeps: int):
        self._lock          = threading.Lock()
        self.max_sweeps     = max_sweeps
        self._current_sweep_freq  = []
        self._current_sweep_ampl  = []
        self._sweep_t_start        = None

        # history of completed sweeps
        self.freq_history   = deque(maxlen=max_sweeps)
        self.ampl_history   = deque(maxlen=max_sweeps)
        self.t_history      = deque(maxlen=max_sweeps)
        self.centroid_hist  = deque(maxlen=max_sweeps)
        self.rms_hist       = deque(maxlen=max_sweeps)

        self.last_update = 0

    def add_point(self, freq_hz: int, ampl: int, t_ms: int):
        """Adds a data point to the current sweep."""
        with self._lock:
            if self._sweep_t_start is None:
                self._sweep_t_start = t_ms

            # detect new sweep (frequency reset)
            if (self._current_sweep_freq and
                    freq_hz < self._current_sweep_freq[-1] * 0.5):
                self._finalize_sweep()

            self._current_sweep_freq.append(freq_hz)
            self._current_sweep_ampl.append(ampl)

    def end_sweep(self):
        """Firmware sent '# SWEEP_END': close the sweep now instead of waiting for the next one."""
        with self._lock:
            if self._current_sweep_freq:
                self._finalize_sweep()

    def _finalize_sweep(self):
        """
        Called internally when a sweep is complete.
        Computes features (same function as 03) and adds them to the history.
        """
        freq = np.array(self._current_sweep_freq, dtype=float)
        ampl = np.array(self._current_sweep_ampl, dtype=float)
        t    = self._sweep_t_start / 1000.0 if self._sweep_t_start is not None else 0.0

        # reset current sweep
        self._current_sweep_freq = []
        self._current_sweep_ampl = []
        self._sweep_t_start       = None

        # keep only complete sweeps (60 points per sweep, starting at the bottom of the range)
        if not (MIN_SWEEP_POINTS <= len(freq) <= N_STEPS and freq[0] <= SWEEP_START_MAX_HZ):
            return

        # features on the RAW arrays (copies, before any normalization)
        feat = extract_feature_single_sweep(freq.copy(), ampl.copy())
        if any(np.isnan(feat[k]) for k in ('centroid_hz', 'rms_abs_db', 'high_freq_ratio')):
            return

        # normalization for the spectrum plot only
        ampl -= ampl.min()
        ampl_lin = np.where(ampl == 0, 1e-9, ampl)
        ampl_lin /= ampl_lin.max()

        # save to history
        self.freq_history.append(freq)
        self.ampl_history.append(ampl_lin)
        self.t_history.append(t)
        self.centroid_hist.append(feat['centroid_hz'])
        self.rms_hist.append(feat['rms_abs_db'])

        self.last_update = time.time()

    def snapshot(self):
        """Returns a thread-safe copy of the current state."""
        with self._lock:
            return {
                'freq_last':    np.array(self.freq_history[-1])    if self.freq_history  else None,
                'ampl_last':    np.array(self.ampl_history[-1])    if self.ampl_history  else None,
                'times':        list(self.t_history),
                'centroids':    list(self.centroid_hist),
                'rms':          list(self.rms_hist),
                'n_sweep':      len(self.t_history),
                'last_update':  self.last_update,
            }


# 
def serial_reader_thread(port: str, buffer: DataBuffer, stop_event: threading.Event):
    """
    Thread that reads the serial port and fills the buffer.
    Runs in the background while matplotlib updates the plot.
    """
    print(f"[SERIAL] Connecting to {port}...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        time.sleep(2)  # wait for Arduino reset
        ser.reset_input_buffer()  # drop bytes left over from before the reset
        # firmware boots idle: without this command no sweep ever starts
        ser.write(b'S')
        print("[CMD] Start sent: S (sweep)")
        print("[SERIAL] Connected. Waiting for data...")

        while not stop_event.is_set():
            try:
                raw = ser.readline()
            except serial.SerialException:
                print("[SERIAL] Connection lost")
                break

            if not raw:
                continue

            try:
                line = raw.decode('ascii', errors='replace').strip()
            except Exception:
                continue

            if line.startswith('# SWEEP_END'):
                buffer.end_sweep()
                continue
            if line.startswith('# SWEEP_START'):
                continue  # one per sweep, would flood the console
            if line.startswith('#'):
                print(f"[ARD] {line}")
                continue

            parts = line.split(',')
            if len(parts) != 4:
                continue

            try:
                freq   = int(parts[0])
                ampl   = int(parts[1])
                t_ms   = int(parts[2])
                mode   = parts[3].strip()
            except ValueError:
                continue

            # accept only sweep data (mode 'S')
            if mode == 'S' and freq > 0:
                buffer.add_point(freq, ampl, t_ms)

        ser.close()

    except serial.SerialException as e:
        print(f"[SERIAL ERR] {e}")
    finally:
        stop_event.set()


# 
def start_monitor(port: str):
    """
    Starts the real-time monitor with matplotlib animation.
    """

    buffer     = DataBuffer(max_sweeps=BUFFER_SWEEP_N)
    stop_event = threading.Event()

    # start serial thread in background
    t = threading.Thread(
        target=serial_reader_thread,
        args=(port, buffer, stop_event),
        daemon=True
    )
    t.start()

    #  FIGURE SETUP 
    fig = plt.figure(figsize=(13, 7))
    fig.patch.set_facecolor('#f8f8f6')

    gs = fig.add_gridspec(2, 2, hspace=0.4, wspace=0.35)

    ax_spectrum   = fig.add_subplot(gs[0, :])   # current spectrum (large)
    ax_centroid  = fig.add_subplot(gs[1, 0])   # centroid over time
    ax_rms       = fig.add_subplot(gs[1, 1])   # RMS over time

    # lines that will be updated
    line_spectrum,     = ax_spectrum.plot([], [], color='#185FA5', lw=1.5)
    line_spectrum_raw, = ax_spectrum.plot([], [], color='#aaaaaa', lw=0.8, alpha=0.5)
    vline_centroid    = ax_spectrum.axvline(0, color='#D85A30', lw=1, ls='--', alpha=0.7)
    line_centroid,    = ax_centroid.plot([], [], color='#185FA5', lw=1.3, marker='o', ms=2)
    line_rms,         = ax_rms.plot([], [], color='#0F6E56', lw=1.3, marker='o', ms=2)

    # fixed axes configuration
    ax_spectrum.set_xlabel('Frequency (Hz)')
    ax_spectrum.set_ylabel('Amplitude (norm.)')
    ax_spectrum.set_title('Spectrum — current sweep')
    ax_spectrum.set_xscale('log')
    ax_spectrum.set_xlim(ANALYSIS_BAND[0], 3500)  # swept band only, as in 02/03
    ax_spectrum.set_ylim(-0.05, 1.1)
    ax_spectrum.grid(True, alpha=0.25, which='both')

    ax_centroid.set_xlabel('Sweep #')
    ax_centroid.set_ylabel('Hz')
    ax_centroid.set_title('Spectral centroid')
    ax_centroid.grid(True, alpha=0.25)

    ax_rms.set_xlabel('Sweep #')
    ax_rms.set_ylabel('dB (ADC)')
    ax_rms.set_title('RMS energy (absolute)')
    ax_rms.grid(True, alpha=0.25)

    fig.suptitle('ACOUSTIC BIOREACTOR — Live monitor', fontsize=11)
    n_sweep_text = fig.text(0.5, 0.02, 'sweep: 0', ha='center', fontsize=8, color='#888888')

    #  ANIMATION UPDATE FUNCTION 
    def update(frame):

        snap = buffer.snapshot()

        if snap['freq_last'] is None:
            return (line_spectrum, line_spectrum_raw,
                    vline_centroid, line_centroid, line_rms)

        #  update spectrum 
        freq = snap['freq_last']
        ampl = snap['ampl_last']

        line_spectrum_raw.set_data(freq, ampl)

        # smooth
        if len(ampl) > 7:
            ampl_s = np.asarray(savgol_filter(ampl, 7, 2))
        else:
            ampl_s = ampl
        line_spectrum.set_data(freq, ampl_s)

        # centroid on the spectrum plot
        if snap['centroids']:
            vline_centroid.set_xdata([snap['centroids'][-1]])

        #  update trend 
        xs = list(range(len(snap['centroids'])))
        if xs:
            line_centroid.set_data(xs, snap['centroids'])
            ax_centroid.set_xlim(0, max(len(xs), 5))
            set_centered_ylim(ax_centroid, pd.Series(snap['centroids']), CENTROID_MIN_HALFSPAN_HZ)

            line_rms.set_data(xs, snap['rms'])
            ax_rms.set_xlim(0, max(len(xs), 5))
            set_centered_ylim(ax_rms, pd.Series(snap['rms']), DB_MIN_HALFSPAN)

        n_sweep_text.set_text(f"sweep: {snap['n_sweep']}")

        return (line_spectrum, line_spectrum_raw,
                vline_centroid, line_centroid, line_rms)

    # start animation
    ani = animation.FuncAnimation(
        fig, update,
        interval=UPDATE_MS,
        blit=False,    # blit=False is more cross-platform compatible
        cache_frame_data=False
    )

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("\n[INFO] Monitor closed")


# 
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Live monitor for the acoustic bioreactor')
    parser.add_argument('--port', default=None,
                        help='Arduino serial port (auto-detected if omitted)')
    args = parser.parse_args()

    port = args.port or find_port()
    if port is None:
        print("[ERR] No port found. Specify it with --port")
        sys.exit(1)

    start_monitor(port)
