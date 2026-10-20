# Copyright 2026 Samuele Bettiol, iGEM Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""
ACOUSTIC SENSOR FILE 1
READS THE DATA SENT BY ARDUINO OVER SERIAL AND SAVES IT TO A CSV FILE.
What it does:
- opens the serial port and starts the sweep as soon as Arduino is ready
- saves every line it receives, with the PC timestamp next to it
- prints a short live log to the terminal
- optionally splits the acquisition into several files, one every N seconds (--split)

#HOW TO USE
# COMPLETE EXAMPLES:
    python 01_data_from_sensor.py --port COM3 --out data/session.csv --duration 900
    python 01_data_from_sensor.py --port COM3 --out data/session.csv --duration 900 --split 300
# OPTIONS:
no --port: the port is found automatically (fine with only one Arduino connected)
--port COM3: pick the port yourself (needed with more than one Arduino)
--out data/session.csv: output file (this is also the default)
--duration 300: stop after 300 s (without it, the acquisition runs until Ctrl+C)
--split 300: a new file every 300 s (data/session_001.csv, session_002.csv, ...)
# send a command to Arduino (S/B/N/R/X) and exit
    python 01_data_from_sensor.py --port COM3 --cmd S

"""
import serial   # pip install pyserial
import csv
import time
import argparse
import sys
from pathlib import Path
from acoustic_common import find_port

# CONFIGURATION
BAUD_RATE    = 115200   # must match BAUD in the Arduino sketch
TIMEOUT_S    = 2.0      # readline() gives up after 2 s without data, then the loop checks --duration and tries again
PRINT_EVERY  = 10       # print one data line in 10: printing all of them slows the loop down

#--------------------------
#       FUNCTION
#--------------------------
def segment_path(base, index: int):
    """
    Name of the file number `index` when the acquisition is split:
    data/session.csv -> data/session_001.csv, data/session_002.csv, ...
    """
    base = Path(base)
    return base.with_name(f"{base.stem}_{index:03d}{base.suffix}")


def open_csv(path):
    """Open the CSV in append mode and write the header only if the file is new/empty."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)   #create a new folder if it does not exist
    csvfile = open(path, 'a', newline='')
    writer  = csv.writer(csvfile)
    if path.stat().st_size == 0:
        writer.writerow([
            'pc_timestamp',     # PC time (unix seconds)
            'freq_hz',          # frequency played by Arduino
            'ampl_adc',         # peak-to-peak amplitude, ADC counts (0-1023)
            'arduino_ms',       # millis() on Arduino
            'mode',             # S/B/N/P/R, META for the '#' lines
            'raw_line'          # the line exactly as received, for debugging
        ])
    return csvfile, writer



def start_logging(port: str, output_path: str | Path, duration_s: float | None = None, split_s: float | None = None):
    """
    Open the serial port and log the CSV lines sent by Arduino.
    ("freq,ampl,timestamp,mode" - ampl is peak-to-peak ADC, 0-1023)

    Args:
        port: serial port (es. 'COM3')
        output_path: the output path of the CSV file
        duration_s: set the max time of the process (None = infinite)
        split_s: start a new numbered file every split_s seconds (None = one single file)
    """
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Opening port {port} @ {BAUD_RATE} baud")
    print(f"[INFO] Output: {output_path}")
    print("[INFO] Press Ctrl+C to stop\n")

    lines_read   = 0            # every line received, comments included
    lines_data   = 0            # valid data lines written to the CSV
    t_start     = time.time()
    segment      = 1            # number of the current file when --split is used
    csvfile      = None

    try:
        # open the serial port
        ser = serial.Serial(port, BAUD_RATE, timeout=TIMEOUT_S)
        ser.reset_input_buffer()  # drop bytes left over from before the reset
        # opening the port resets Arduino, which boots idle: the start command 'S' is sent
        # when it prints "# READY" (see below), so the boot header is saved in the CSV too

        # open the first file (numbered only when the acquisition is split)
        current_path = segment_path(output_path, segment) if split_s else Path(output_path)
        csvfile, writer = open_csv(current_path)
        print(f"[FILE] Writing: {current_path}")
        t_segment = time.time()   # start time of the current file

        while True:

            if duration_s and (time.time() - t_start) > duration_s:
                print(f"\n[INFO] Duration of {duration_s}s reached, stopping.")
                break

            try:
                raw = ser.readline()
            except serial.SerialException as e: # usually the USB cable came out
                print(f"[ERR] Serial: {e}")
                break

            if not raw:
                continue  # timeout, nothing arrived

            try:
                line = raw.decode('ascii', errors='replace').strip() 
            except Exception:
                continue

            lines_read += 1

            # comment lines from Arduino (they start with '#')
            if line.startswith('#'):
                # the current file is full: close it and open the next one (the serial port stays open)
                # rotate only at the start of a sweep, so no sweep is split between two files
                if split_s and line.startswith('# SWEEP_START') and (time.time() - t_segment) > split_s:
                    csvfile.close()
                    print(f"[FILE] Closed: {current_path} ({lines_data} valid lines so far)")
                    segment += 1
                    current_path = segment_path(output_path, segment)
                    csvfile, writer = open_csv(current_path)
                    print(f"[FILE] Writing: {current_path}")
                    t_segment = time.time()

                print(f"[ARD] {line}")
                # saved as META: the loaders in acoustic_common look for "# READY" here
                writer.writerow([
                    time.time(), 0, 0, 0, 'META', line
                ])
                # Arduino has (re)booted and is idle: start the sweep
                if line == '# READY':
                    ser.write(b'S')
                    print("[CMD] Start sent: S (sweep)")
                continue

            # data line: freq,ampl,timestamp,mode
            part = line.split(',')
            if len(part) != 4:
                continue  # truncated line, or RAW mode (one value per line): not saved

            try:
                freq      = int(part[0])
                ampl      = int(part[1])
                ard_ms    = int(part[2])
                mode_char = part[3].strip()
            except ValueError:
                continue  # non-numeric field, skip the line

            pc_ts = time.time() # PC clock: puts the rows on real time, arduino_ms restarts at every reset

            writer.writerow([pc_ts, freq, ampl, ard_ms, mode_char, line])

            lines_data += 1

            # flush every 50 lines: if the PC crashes, only the last few seconds are lost
            if lines_data % 50 == 0:
                csvfile.flush()

            if lines_data % PRINT_EVERY == 0:
                elapsed = time.time() - t_start
                print(
                    f"  t={elapsed:6.1f}s | "
                    f"mode={mode_char} | "
                    f"freq={freq:6d}Hz | "
                    f"ampl={ampl:4d} | "
                    f"tot={lines_data}"
                )

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by the user...")  # Ctrl+C

    except serial.SerialException as e:
        print(f"[ERR] Cannot open port {port}: {e}") # wrong port, or already open in the Arduino IDE
        sys.exit(1)

    finally: 
        if csvfile:
            csvfile.close()
        elapsed = time.time() - t_start 
        print(f"\n[DONE] Lines read: {lines_read} | Valid data: {lines_data} | Time: {elapsed:.1f}s")
        if split_s:
            print(f"[DONE] Files saved: {segment} ({segment_path(output_path, 1)} ... {segment_path(output_path, segment)})")
        else:
            print(f"[DONE] File saved: {output_path}")


def send_command(port: str, command: str):
    """
    Send a single command to Arduino and close the port.
    Command: 'S'=sweep, 'B'=burst, 'N'=noise, 'R'=raw, 'X'=stop
    """
    ser = serial.Serial(port, BAUD_RATE, timeout=1)
    time.sleep(2) # wait for the Arduino reset triggered by opening the port
    ser.reset_input_buffer() # drop bytes left over from before the reset
    ser.write(command.encode())
    ser.close()
    print(f"[CMD] Sent: {command}")

#--------------------------------
#        MAIN
#-------------------------------
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Serial logger for the acoustic bioreactor')
    parser.add_argument('--port',  default=None,             help='Serial port (auto-detected if omitted)')
    parser.add_argument('--out',   default='data/session.csv', help='Output CSV file')
    parser.add_argument('--duration',type=float, default=None, help='Acquisition time in seconds (default: unlimited)')
    parser.add_argument('--split', type=float, default=None, help='Split the acquisition: a new numbered file every N seconds')
    parser.add_argument('--cmd',   default=None,             help='Send a command to Arduino and exit (S/B/N/R/X)')
    args = parser.parse_args()

    port = args.port or find_port()
    if port is None:
        print("[ERR] Specify the port with --port")
        sys.exit(1)

    # --cmd: send one command and exit, no logging
    if args.cmd:
        send_command(port, args.cmd)
        sys.exit(0)

    start_logging(port, args.out, args.duration, args.split)

