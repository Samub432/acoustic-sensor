"""

ACUSTIC SENSOR FILE 5
OPTICAL BUBBLE COUNTING FROM VIDEO

Counts O2 bubbles released by an aquatic plant from a fixed-camera video
(black background, side light) so the optical bubble rate can be correlated
with the acoustic features produced by 03_features.py.

METHOD:
- a narrow horizontal ROI (the "counting line") is selected once with the mouse,
  just above the emission point
- MOG2 background subtraction runs ONLY inside that ROI
- the foreground mask is thresholded + morphologically opened, then blobs are
  extracted with connectedComponentsWithStats
- blobs are matched frame-to-frame by nearest neighbour (bubbles travel upwards,
  so the vertical axis carries the motion): a bubble is counted once, when its
  track is created, and never again while it stays inside the ROI
- counts are binned per second using the REAL frame rate of the video

The video is processed in streaming (one frame at a time), so length does not
matter for memory.

HOW TO USE:
# first run: draw the ROI with the mouse, confirm with ENTER (ESC/c cancels)
    python 05_bubble_count.py video.mp4
# tune the parameters while watching (q = quit, space = pause)
    python 05_bubble_count.py video.mp4 --debug
# reuse the same ROI (the script prints it at startup, copy it back)
    python 05_bubble_count.py video.mp4 --roi 320,540,60,20
# analyse only a time window, and choose the output file
    python 05_bubble_count.py video.mp4 --start 30 --end 300 --out bubbles.csv
# run the internal self-check of the tracking logic
    python 05_bubble_count.py --selftest

OUTPUT: CSV with columns time_s, bubbles_per_second

SPEED: when ffmpeg is on PATH (or the FFMPEG env var points at it) the frames
are decoded and cropped by ffmpeg, which is ~3x faster than OpenCV on 4K.
--debug always uses OpenCV, because it has to draw the full frame.
"""

import argparse
import csv
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# TUNABLE PARAMETERS  (see the table in the README / tune them with --debug)
# ---------------------------------------------------------------------------
THRESH = 200               # binarisation of the MOG2 mask (127 = shadow, 255 = foreground)
MIN_AREA = 12              # minimum blob area in pixels -> rejects sensor noise
MAX_AREA = 5000            # maximum blob area in pixels -> rejects leaves / light reflections
ROI_HEIGHT = 0             # forced ROI height in pixels (0 = keep the height drawn with the mouse)
MOG2_LEARNING_RATE = 0.01  # how fast a still object is absorbed into the background
MOG2_HISTORY = 500         # frames kept in the background model
MOG2_VAR_THRESHOLD = 24    # sensitivity of the background model (lower = more sensitive)
MATCH_DIST_PX = 25         # frame-to-frame association radius (anti double counting)
TRACK_TIMEOUT_FRAMES = 4   # frames a track survives without a detection
WARMUP_FRAMES = 30         # frames discarded while MOG2 learns the background
MORPH_KERNEL = 3           # opening kernel size, odd; 0 disables the morphological filter
# ---------------------------------------------------------------------------

# ffmpeg decodes 4K ~3x faster than OpenCV because it crops to the ROI and
# converts to gray before the frame ever reaches Python. Used automatically
# when available; set the FFMPEG env var if it is not on PATH.
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")


def ffmpeg_frames(path, start_s, roi):
    """Yield (gray ROI patch, None) decoding with ffmpeg instead of OpenCV."""
    assert FFMPEG, "called only when ffmpeg is available"
    rx, ry, rw, rh = roi
    proc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-ss", str(start_s), "-i", path,
         "-vf", f"crop={rw}:{rh}:{rx}:{ry},format=gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, bufsize=rw * rh * 16)
    assert proc.stdout is not None
    nbytes = rw * rh
    try:
        while True:
            buf = proc.stdout.read(nbytes)
            if len(buf) < nbytes:            # short read = end of stream
                return
            yield np.frombuffer(buf, np.uint8).reshape(rh, rw), None
    finally:
        proc.terminate()          # --end / an early break must not leave ffmpeg writing
        proc.stdout.close()
        proc.wait()


def cv2_frames(cap, frame, roi):
    """Yield (gray ROI patch, full BGR frame) reading with OpenCV."""
    rx, ry, rw, rh = roi
    while True:
        yield cv2.cvtColor(frame[ry:ry + rh, rx:rx + rw], cv2.COLOR_BGR2GRAY), frame
        ok, frame = cap.read()
        if not ok:
            return


def detect_blobs(mask):
    """Return the blobs of a binary mask as (cx, cy, area, x, y, w, h)."""
    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = []
    for i in range(1, n):  # label 0 is the background
        area = stats[i, cv2.CC_STAT_AREA]
        if not (MIN_AREA <= area <= MAX_AREA):
            continue
        cx, cy = centroids[i]
        blobs.append((cx, cy, int(area),
                      stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                      stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]))
    return blobs


def update_tracks(tracks, blobs, frame_i,
                  match_dist=MATCH_DIST_PX, timeout=TRACK_TIMEOUT_FRAMES):
    """Greedy nearest-neighbour tracking inside the ROI.

    Drops stale tracks, matches every blob to the closest surviving track and
    creates a track for the unmatched ones. Returns how many tracks were
    created, i.e. how many NEW bubbles entered the counting line.
    """
    # ponytail: greedy NN, no motion prediction. Two bubbles merging into one
    # blob count as one; use a proper tracker only if that shows up in --debug.
    tracks[:] = [t for t in tracks if frame_i - t["last"] <= timeout]

    pairs = sorted(
        (float(np.hypot(b[0] - t["x"], b[1] - t["y"])), bi, ti)
        for bi, b in enumerate(blobs)
        for ti, t in enumerate(tracks)
    )
    used_b, used_t = set(), set()
    for dist, bi, ti in pairs:
        if dist > match_dist or bi in used_b or ti in used_t:
            continue
        tracks[ti].update(x=blobs[bi][0], y=blobs[bi][1], last=frame_i)
        used_b.add(bi)
        used_t.add(ti)

    new = 0
    for bi, b in enumerate(blobs):
        if bi in used_b:
            continue
        tracks.append({"x": b[0], "y": b[1], "last": frame_i})
        new += 1
    return new


def select_roi(frame):
    """Ask the user to drag the counting line, return (x, y, w, h)."""
    x, y, w, h = cv2.selectROI("Draw the counting ROI, then ENTER", frame,
                               showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        sys.exit("No ROI selected.")
    if ROI_HEIGHT > 0:  # keep the same centre, force the configured height
        y = max(0, y + (h - ROI_HEIGHT) // 2)
        h = min(ROI_HEIGHT, frame.shape[0] - y)
    return int(x), int(y), int(w), int(h)


def run(path, start_s, end_s, roi, out_path, debug):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"Cannot open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps <= 0:  # missing or NaN in some containers
        fps = 30.0
        print("WARNING: frame rate not reported by the file, assuming 30 fps")
    print(f"Video: {path}  |  {fps:.3f} fps")

    if start_s > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))

    ok, frame = cap.read()
    if not ok:
        sys.exit("Empty video, or --start beyond the end of the file.")
    if roi is None:
        roi = select_roi(frame)
    print(f"ROI = {roi[0]},{roi[1]},{roi[2]},{roi[3]}   (reuse it with --roi)")
    rx, ry, rw, rh = roi

    bgsub = cv2.createBackgroundSubtractorMOG2(
        history=MOG2_HISTORY, varThreshold=MOG2_VAR_THRESHOLD, detectShadows=True)
    kernel = (cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL, MORPH_KERNEL))
              if MORPH_KERNEL >= 3 else None)

    if debug or not FFMPEG:                        # --debug needs the full frame
        source = cv2_frames(cap, frame, roi)
        if not FFMPEG and not debug:
            print("NOTE: ffmpeg not found, decoding with OpenCV (~3x slower)")
    else:
        cap.release()
        source = ffmpeg_frames(path, start_s, roi)

    tracks, per_second, total = [], [], 0
    for frame_i, (patch, frame) in enumerate(source):
        t_rel = frame_i / fps                      # seconds since --start
        if end_s is not None and start_s + t_rel > end_s:
            break

        mask = bgsub.apply(patch, learningRate=MOG2_LEARNING_RATE)
        _, mask = cv2.threshold(mask, THRESH, 255, cv2.THRESH_BINARY)
        if kernel is not None:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        blobs = detect_blobs(mask)
        new = update_tracks(tracks, blobs, frame_i)
        if frame_i >= WARMUP_FRAMES:               # ignore the background learning phase
            total += new
            sec = int(t_rel)
            while len(per_second) <= sec:
                per_second.append(0)
            per_second[sec] += new

        if debug:
            assert frame is not None  # --debug always decodes with OpenCV (full frame)
            view = frame.copy()
            cv2.rectangle(view, (rx, ry), (rx + rw, ry + rh), (0, 255, 255), 1)
            for _, _, area, bx, by, bw, bh in blobs:
                cv2.rectangle(view, (rx + bx, ry + by),
                              (rx + bx + bw, ry + by + bh), (0, 255, 0), 1)
                cv2.putText(view, str(area), (rx + bx, ry + by - 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
            state = "WARMUP" if frame_i < WARMUP_FRAMES else f"bubbles: {total}"
            cv2.putText(view, f"t={start_s + t_rel:7.2f}s  {state}", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow("video", view)
            cv2.imshow("roi mask", mask)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord(" "):
                while (cv2.waitKey(50) & 0xFF) != ord(" "):
                    pass
        elif frame_i % 1000 == 0:
            print(f"  t={start_s + t_rel:8.1f}s  bubbles={total}", flush=True)

    cap.release()
    cv2.destroyAllWindows()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "bubbles_per_second"])
        for sec, n in enumerate(per_second):
            w.writerow([round(start_s + sec, 3), n])

    dur = len(per_second)
    print(f"\nTotal bubbles: {total} over {dur} s "
          f"({total / dur if dur else 0:.2f} bubbles/s average)")
    print(f"Saved: {out_path}")


def selftest():
    """Minimal check of the counting logic (no video needed)."""
    # a single bubble rising through the ROI is counted once
    tracks = []
    counts = [update_tracks(tracks, [(30.0, 40.0 - 8 * i, 20, 0, 0, 5, 5)], i)
              for i in range(5)]
    assert counts == [1, 0, 0, 0, 0], counts

    # two bubbles at different x in the same frame are counted separately
    tracks = []
    assert update_tracks(tracks, [(10.0, 20.0, 20, 0, 0, 5, 5),
                                  (90.0, 20.0, 20, 0, 0, 5, 5)], 0) == 2

    # a track that disappears for longer than the timeout is dropped
    tracks = [{"x": 10.0, "y": 20.0, "last": 0}]
    update_tracks(tracks, [], TRACK_TIMEOUT_FRAMES + 1)
    assert tracks == []

    # a jump larger than MATCH_DIST_PX is a different bubble, not the same one
    tracks = []
    update_tracks(tracks, [(10.0, 20.0, 20, 0, 0, 5, 5)], 0)
    assert update_tracks(tracks, [(10.0, 20.0 + MATCH_DIST_PX * 2, 20, 0, 0, 5, 5)], 1) == 1

    # blobs outside the area window are ignored
    mask = np.zeros((50, 50), np.uint8)
    mask[10:12, 10:12] = 255                 # 4 px  -> below MIN_AREA
    mask[20:30, 20:30] = 255                 # 100 px -> kept
    assert len(detect_blobs(mask)) == 1

    # the ffmpeg reader yields one correctly shaped patch per frame
    if FFMPEG:
        import tempfile
        clip = os.path.join(tempfile.gettempdir(), "_bubble_selftest.mp4")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc=size=64x48:rate=10:duration=1", clip], check=True)
        patches = [p for p, _ in ffmpeg_frames(clip, 0, (8, 4, 32, 16))]
        assert len(patches) == 10, len(patches)
        assert patches[0].shape == (16, 32), patches[0].shape
        os.remove(clip)
    else:
        print("(ffmpeg not found, its reader was not tested)")

    print("selftest OK")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Count O2 bubbles crossing a horizontal line in a video.")
    p.add_argument("video", nargs="?", help="path to the video file")
    p.add_argument("--start", type=float, default=0.0, help="start time in seconds")
    p.add_argument("--end", type=float, default=None, help="end time in seconds")
    p.add_argument("--roi", help="reuse a ROI as x,y,w,h instead of drawing it")
    p.add_argument("--out", help="output CSV (default: <video>_bubbles.csv)")
    p.add_argument("--debug", action="store_true", help="show video, blobs and live counter")
    p.add_argument("--selftest", action="store_true", help="run the internal check and exit")
    a = p.parse_args()

    if a.selftest:
        selftest()
        sys.exit(0)
    if not a.video:
        p.error("the video path is required (or use --selftest)")

    roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else None
    if roi is not None and len(roi) != 4:
        p.error("--roi must be x,y,w,h")
    out = a.out or os.path.splitext(a.video)[0] + "_bubbles.csv"
    run(a.video, a.start, a.end, roi, out, a.debug)
