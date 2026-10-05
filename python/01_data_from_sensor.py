"""

ACUSTIC SENSOR FILE 1
THIS FILE READS DATA FROM THE SERIAL PORT AND SAVES IT TO A CSV FILE.
What it does:
- opens the serial port
- reads the line of the CSV data send by arduino
- it saves the data in a CSV data file with timestamp (it saves the time to give a temporal reference to every data line)
- print a data log on the terminal
- optionally splits the acquisition in several files, one every N seconds (--split)

#HOW TO USE
# COMPLETE EXAMPLES:
    python 01_data_from_sensor.py --port COM3 --out data/session.csv --duration 900
    python 01_data_from_sensor.py --port COM3 --out data/session.csv --duration 900 --split 300
# PARTIAL EXAMPLES:
auto-detect the port (if there is only one Arduino connected): python 01_data_from_sensor.py
specify the port (if there are more than one Arduino connected): --port COM3
output file (default: data/session.csv) for the change in the output file name change session.csv: --out data/session.csv 
limit the duration of the acquisition (in seconds) (without --duration there is a continuous acquisition): --duration 300
split the acquisition in one file every N seconds (data/session_001.csv, session_002.csv, ...): --split 300
# send a command to Arduino (S/B/N/R/X) and exit
    python 01_data_from_sensor.py --port COM3 --cmd S

"""
#IMPORT THE LIBRARY
import serial   # pip install pyserial
import csv      # for the csv file
import time     # import time to manage the timestamp of the data
import argparse # to read the arguments of the command line (the port and the output file)
import sys      # for the fast exit of the program
from pathlib import Path #to manage the path of the output of the files
from acoustic_common import find_port

# CONFIGURATION
BAUD_RATE    = 115200   #serial velocity (it must be the same of Arduino)
TIMEOUT_S    = 2.0      #to stop the program if the data are not received for 2 seconds
PRINT_EVERY  = 10       #to not slow the program with the log live, it will stamp every 10 lines

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
            'pc_timestamp',     # timestamp PC (unix)
            'freq_hz',          # frequency that is generated form Arduino
            'ampl_adc',         # peak-to-peak amplitude ADC (0-1023)
            'arduino_ms',       # millis() Arduino
            'mode',             # S/B/N/P/R
            'raw_line'          # lines of the file for debug
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
    
    # Preliminary preparation of the file
    output_path = Path(output_path)                         #string to path
    output_path.parent.mkdir(parents=True, exist_ok=True)   #create a new folder if it does not exist

    print(f"[INFO] Opening port {port} @ {BAUD_RATE} baud")
    print(f"[INFO] Output: {output_path}")
    print("[INFO] Press Ctrl+C to stop\n")

    lines_read   = 0            #lines that are received of the CSV file
    lines_data   = 0            #lines that are valid of the CSV file
    t_start     = time.time()   #start time of the session
    segment      = 1            #number of the current file when --split is used
    csvfile      = None         #file currently open

    try:
        # open the serial port
        ser = serial.Serial(port, BAUD_RATE, timeout=TIMEOUT_S)
        time.sleep(2)  # wait for the Arduino reset triggered by opening the port (skipping this loses the first lines)
        ser.reset_input_buffer()  # drop bytes left over from before the reset

        # Arduino boots in idle: send the start command so the sweep begins only now
        ser.write(b'S')
        print("[CMD] Start sent: S (sweep)")

        # open the first file (numbered only when the acquisition is split)
        current_path = segment_path(output_path, segment) if split_s else Path(output_path)
        csvfile, writer = open_csv(current_path)
        print(f"[FILE] Writing: {current_path}")
        t_segment = time.time()   #start time of the current file

        while True:

            # see if the time of the session is right, after a set time it will stop 
            if duration_s and (time.time() - t_start) > duration_s:
                print(f"\n[INFO] Duration of {duration_s}s reached, stopping.")
                break

            # the current file is full: close it and open the next one (the serial port stays open)
            if split_s and (time.time() - t_segment) > split_s:
                csvfile.close()
                print(f"[FILE] Closed: {current_path} ({lines_data} valid lines so far)")
                segment += 1
                current_path = segment_path(output_path, segment)
                csvfile, writer = open_csv(current_path)
                print(f"[FILE] Writing: {current_path}")
                t_segment = time.time()

            # read all the serial lines
            try:
                raw = ser.readline() 
            except serial.SerialException as e: # for the accidental disconnection of the cable or for similar problem
                print(f"[ERR] Serial: {e}")
                break

            if not raw:
                continue  # if there is nothing, continue

            # bytes → string
            try:
                line = raw.decode('ascii', errors='replace').strip() 
            except Exception:
                continue

            lines_read += 1

            # comment lines from Arduino (they start with '#')
            if line.startswith('#'):
                print(f"[ARD] {line}")
                # save the metadata
                writer.writerow([
                    time.time(), 0, 0, 0, 'META', line
                ])
                continue

            # lines CSV data (4 fields: freq,ampl,timestamp,mode)
            part = line.split(',')
            if len(part) != 4:
                continue  # malformed or truncated line, skip it

            try:
                freq      = int(part[0])
                ampl      = int(part[1])
                ard_ms    = int(part[2])
                mode_char = part[3].strip()
            except ValueError:
                continue  # non-numeric field, skip the line

            pc_ts = time.time() #to synchronize with the pc

            # write in the CSV, save all the data
            writer.writerow([pc_ts, freq, ampl, ard_ms, mode_char, line])

            lines_data += 1

            # periodic flush (every 50 lines, less lines is better)
            if lines_data % 50 == 0:
                csvfile.flush()

            # print on the terminal every PRINT_EVERY lines (not every lines)
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
        print(f"[ERR] Cannot open port {port}: {e}") #if the port does not exist
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

    # find the port (auto select if is not specified)
    port = args.port or find_port()
    if port is None:
        print("[ERR] Specify the port with --port")
        sys.exit(1)

    # send the command without the start of the logging
    if args.cmd:
        send_command(port, args.cmd)
        sys.exit(0)

    # start logging
    start_logging(port, args.out, args.duration, args.split)

