"""Self-check for the --split file rotation (run: python test_split.py)."""
import importlib.util, tempfile, csv
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

print("ok")
