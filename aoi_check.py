# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib", "numpy"]
# ///
"""Quick visual check of a recorded session: for every reading trial,
the logged word AOIs and the raw gaze samples are drawn over the
trial's screenshot. Calibration drift, a vertical offset or a
misplaced AOI is obvious at a glance here.

Usage
    uv run aoi_check.py data/logs/<prefix>_log.tsv
    uv run aoi_check.py data/logs/<prefix>_log.tsv --out somewhere/

Everything is read from the session's own files: the log row carries
the AOI rectangles (metadata1) and the eye-CSV path (metadata2), the
screenshot column names the PNG. AOIs, gaze and screenshot all use
screen pixels with the origin at the top left, so everything is drawn
as it is. The images go to data/checks/ by default."""

import argparse
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")   # files only, no display needed
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

EYE_LOST_SENTINEL = 32767


def read_log(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_aois(metadata1):
    """The metadata1 string -> [(left, top, right, bottom), ...] in
    pixels."""
    boxes = []
    for rect in metadata1.split(";"):
        left, top, right, bottom = (float(v) for v in rect.split(","))
        boxes.append((left, top, right, bottom))
    return boxes


def read_gaze(path):
    """The trial's eye CSV -> ([xL], [yL], [xR], [yR]) in pixels,
    eye-lost samples (TRACKPixx 32767, EyeLink -32768) and the empty
    cells of an unrecorded eye dropped. Header-only files (dry-run) and
    trials without an eye CSV come back empty."""
    xs_l, ys_l, xs_r, ys_r = [], [], [], []
    if not path:
        return xs_l, ys_l, xs_r, ys_r
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for xs, ys, cx, cy in ((xs_l, ys_l, "LeftEyeX", "LeftEyeY"),
                                   (xs_r, ys_r, "RightEyeX", "RightEyeY")):
                if not row[cx] or not row[cy]:
                    continue
                x, y = float(row[cx]), float(row[cy])
                if abs(x) >= EYE_LOST_SENTINEL or abs(y) >= EYE_LOST_SENTINEL:
                    continue
                xs.append(x)
                ys.append(y)
    return xs_l, ys_l, xs_r, ys_r


def check_trial(row, log_dir, out_dir):
    shot = os.path.join(log_dir, "..", "screenshots", row["screenshot"])
    if not os.path.isfile(shot):
        print(f"  page {row['pno']}: screenshot missing, skipped ({shot})")
        return
    img = plt.imread(shot)
    H, W = img.shape[0], img.shape[1]

    boxes = parse_aois(row["metadata1"])

    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    ax.imshow(img)
    for i, (left, top, right, bottom) in enumerate(boxes):
        ax.add_patch(Rectangle((left, top), right - left, bottom - top,
                               fill=False,
                               edgecolor="deepskyblue", linewidth=0.8))
        ax.annotate(str(i), (left, top - 3), color="deepskyblue", fontsize=7,
                    ha="left", va="bottom")
    eye_csv = row["metadata2"]
    xs_l, ys_l, xs_r, ys_r = read_gaze(
        os.path.join(log_dir, "..", "..", eye_csv) if eye_csv else None)
    ax.scatter(xs_l, ys_l, s=4, color="cyan", alpha=0.5, label="left eye")
    ax.scatter(xs_r, ys_r, s=4, color="magenta", alpha=0.5,
               label="right eye")

    note = (f"page {row['pno']}  item {row['item']}/{row['condition']}  "
            f"{len(boxes)} AOIs, {len(xs_l) + len(xs_r)} gaze samples")
    ax.set_title(note, fontsize=9)
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    name = os.path.splitext(row["screenshot"])[0] + "_check.png"
    out = os.path.join(out_dir, name)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  {note}\n    -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", help="a session's _log.tsv")
    ap.add_argument("--out", default=None,
                    help="output folder (default: data/checks)")
    args = ap.parse_args()

    log_dir = os.path.dirname(os.path.abspath(args.log))
    out_dir = args.out or os.path.join(log_dir, "..", "checks")
    os.makedirs(out_dir, exist_ok=True)

    trials = [row for row in read_log(args.log)
              if "ReadingTrial" in row["type"] and row["metadata1"]]
    if not trials:
        sys.exit("No reading trials with AOIs in this log.")
    print(f"{len(trials)} reading trials in {args.log}")
    for row in trials:
        check_trial(row, log_dir, out_dir)


if __name__ == "__main__":
    main()
