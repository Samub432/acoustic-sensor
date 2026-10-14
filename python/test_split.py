# Copyright 2026 Samuele Bettiol and the iGEM 2026 Uni-Padua-IT team
# SPDX-License-Identifier: MIT
"""Self-check for the --split file rotation (run: python test_split.py)."""
import importlib.util, tempfile, csv, contextlib, io
from pathlib import Path

spec = importlib.util.spec_from_file_location("logger", Path(__file__).with_name("01_data_from_sensor.py"))
assert spec and spec.loader
logger = importlib.util.module_from_spec(spec); spec.loader.exec_module(logger)

# numbering
assert str(logger.segment_path("data/session.csv", 1)).endswith("session_001.csv")
assert str(logger.segment_path(Path("data/session.csv"), 12)).endswith("session_012.csv")

# header written once per new file, not on re-open
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "sub" / "session_001.csv"
    f, w = logger.open_csv(p); w.writerow([1, 2, 3, 4, 'S', 'x']); f.close()
    f, w = logger.open_csv(p); w.writerow([5, 6, 7, 8, 'S', 'y']); f.close()
    rows = list(csv.reader(p.open()))
    assert rows[0][0] == 'pc_timestamp' and len(rows) == 3, rows

# start_logging with a fake port: 'S' sent on every "# READY" (boot and reset mid-session),
# boot header saved, files rotated only at "# SWEEP_START" (no sweep split between files)
class FakeSerial:
    lines = ["2000,50,99999,S", "# ACOUSTIC BIOREACTOR v1.0", "# READY",
             "# SWEEP_START,375", "1000,40,400,S", "3427,10,5300,S", "# SWEEP_END,5359",
             "0,512,5400,P",
             "# SWEEP_START,10384", "1000,41,10400,S", "3427,11,15300,S", "# SWEEP_END,15371",
             "# READY"]
    def __init__(self, *a, **k): self.it = iter(self.lines); self.sent = []; FakeSerial.inst = self
    def reset_input_buffer(self): pass
    def write(self, b): self.sent.append(b)
    def readline(self):
        try: return (next(self.it) + "\r\n").encode()
        except StopIteration: raise logger.serial.SerialException("end of fake data")

logger.serial.Serial = FakeSerial
with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
    logger.start_logging("FAKE", Path(d) / "s.csv", split_s=-1)  # always "full"
    # split_s < 0 -> a new file at every "# SWEEP_START": boot lines / sweep 1 / sweep 2
    f1, f2, f3 = [[r[5] for r in csv.reader(logger.segment_path(Path(d) / "s.csv", i).open())]
                  for i in (1, 2, 3)]
assert FakeSerial.inst.sent == [b'S', b'S'], FakeSerial.inst.sent
assert f1[1:] == ["2000,50,99999,S", "# ACOUSTIC BIOREACTOR v1.0", "# READY"], f1
assert f2[1] == "# SWEEP_START,375" and f2[-1] == "0,512,5400,P", f2
assert f3[1] == "# SWEEP_START,10384" and f3[-1] == "# READY", f3

print("ok")
