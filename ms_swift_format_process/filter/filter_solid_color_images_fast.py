#!/usr/bin/env python3
"""Fast parallel filter for solid-color images in tar shards.

Speedups vs. ``filter_solid_color_images.py``:

* Parallelizes across tar shards with ``multiprocessing`` (each worker
  handles whole tar files independently).
* Streams members in tar order instead of building a per-tar member
  index and doing random-access ``extractfile`` lookups -- much faster
  on networked / shared filesystems.
* Uses PIL ``getextrema()`` for the default ``--std-threshold 0`` case
  (no numpy conversion of every pixel needed -- the image is solid iff
  every channel has equal min and max).
* For non-zero thresholds, computes std on a 128x128 thumbnail rather
  than the full image.

Semantics match the baseline filter:
* A JSONL entry is rejected if ANY of its referenced images is
  solid-color or fails to decode.
* Output line order is preserved.
"""

import argparse
import io
import json
import os
import tarfile
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def _check_image_bytes(data: bytes, std_threshold: float):
    """Return (is_solid, info). ``info`` is std value or error string."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if std_threshold <= 0.0:
            extrema = img.getextrema()
            if isinstance(extrema[0], tuple):
                is_solid = all(lo == hi for (lo, hi) in extrema)
            else:
                lo, hi = extrema
                is_solid = lo == hi
            return is_solid, 0.0 if is_solid else -1.0
        img.thumbnail((128, 128))
        std = float(np.asarray(img).std())
        return std <= std_threshold, std
    except Exception as e:
        return False, f"ERROR: {e}"


def _process_tar(task):
    """Worker: stream a single tar, check every wanted member.

    ``wanted`` maps member_name -> list of (line_idx, image_idx) pairs
    referencing it (a single image may appear in multiple entries).
    Returns a list of (line_idx, image_idx, status, info) tuples where
    ``status`` is "solid", "ok", or "error".
    """
    tar_path, wanted, std_threshold = task
    out = []
    try:
        with tarfile.open(tar_path, "r") as tf:
            remaining = {k: list(v) for k, v in wanted.items()}
            for member in tf:
                if not remaining:
                    break
                clean = member.name.lstrip("./")
                refs = remaining.pop(clean, None)
                if refs is None:
                    continue
                if not member.isfile():
                    for li, ii in refs:
                        out.append((li, ii, "error", "not a file"))
                    continue
                f = tf.extractfile(member)
                if f is None:
                    for li, ii in refs:
                        out.append((li, ii, "error", "extractfile returned None"))
                    continue
                data = f.read()
                is_solid, info = _check_image_bytes(data, std_threshold)
                if isinstance(info, str) and info.startswith("ERROR:"):
                    for li, ii in refs:
                        out.append((li, ii, "error", info[len("ERROR: "):]))
                else:
                    status = "solid" if is_solid else "ok"
                    for li, ii in refs:
                        out.append((li, ii, status, info))
            for member_name, refs in remaining.items():
                for li, ii in refs:
                    out.append((li, ii, "error", "missing in tar"))
    except Exception as e:
        for member_name, refs in wanted.items():
            for li, ii in refs:
                out.append((li, ii, "error", f"tar open failed: {e}"))
    return tar_path.name, out


def main():
    parser = argparse.ArgumentParser(
        description="Fast parallel filter for solid-color images in tar shards",
    )
    parser.add_argument("--jsonl", required=True, help="Path to input JSONL file")
    parser.add_argument("--image_root", required=True,
                        help="Directory containing tar files")
    parser.add_argument("--output", default=None,
                        help="Output JSONL path (default: <input>_solid_filtered.jsonl)")
    parser.add_argument("--std-threshold", type=float, default=0.0,
                        help="Std-dev threshold; images with std <= this are filtered (default: 0.0)")
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4)),
                        help="Worker processes (default: all cores)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every filtered/error image")
    args = parser.parse_args()

    if args.output is None:
        p = Path(args.jsonl)
        args.output = str(p.with_stem(p.stem + "_solid_filtered"))

    if not Path(args.jsonl).exists():
        print(f"Error: Input file not found: {args.jsonl}")
        return 1

    print(f"Reading {args.jsonl}...")
    with open(args.jsonl) as f:
        lines = f.readlines()
    total = len(lines)
    print(f"Total entries: {total}")
    print(f"Std-dev threshold: {args.std_threshold}")
    print(f"Workers: {args.workers}")

    # Group: tar_name -> {member_name: [(line_idx, image_idx)]}
    print("Grouping entries by tar shard...")
    by_tar = defaultdict(lambda: defaultdict(list))
    decisions = [None] * total  # None = keep so far, "solid" / "error" = reject.
    json_errors = 0
    path_errors = 0
    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            json_errors += 1
            decisions[i] = "error"
            continue
        for ii, img_rel in enumerate(entry.get("images") or []):
            parts = img_rel.split("/", 1)
            if len(parts) != 2:
                path_errors += 1
                if decisions[i] is None:
                    decisions[i] = "error"
                continue
            tar_name, member_name = parts
            by_tar[tar_name][member_name].append((i, ii))

    image_root = Path(args.image_root)
    tasks = []
    for tar_name, wanted in by_tar.items():
        tar_path = image_root / tar_name
        tasks.append((tar_path, dict(wanted), args.std_threshold))
    # Largest shards first -- helps Pool finish around the same time.
    tasks.sort(key=lambda t: -len(t[1]))
    print(f"Tar shards to scan: {len(tasks)}")

    solid_count = 0
    error_count = json_errors + path_errors

    done_tars = 0
    progress_every = max(1, len(tasks) // 200)
    print("Scanning tar shards in parallel...")

    with Pool(processes=args.workers) as pool:
        for tar_name, results in pool.imap_unordered(_process_tar, tasks):
            for line_idx, image_idx, status, info in results:
                if status == "solid":
                    if decisions[line_idx] is None:
                        decisions[line_idx] = "solid"
                        solid_count += 1
                        if args.verbose:
                            print(f"  [FILTERED] line {line_idx}: solid-color {tar_name}#img{image_idx}")
                elif status == "error":
                    if decisions[line_idx] is None:
                        decisions[line_idx] = "error"
                        error_count += 1
                        if args.verbose:
                            print(f"  [ERROR] line {line_idx}: {info} ({tar_name}#img{image_idx})")
            done_tars += 1
            if done_tars % progress_every == 0 or done_tars == len(tasks):
                print(
                    f"  Shards: {done_tars}/{len(tasks)} | "
                    f"solid: {solid_count} | errors: {error_count}",
                    flush=True,
                )

    print("Writing output...")
    kept = 0
    with open(args.output, "w") as f:
        for i, line in enumerate(lines):
            if decisions[i] is None:
                f.write(line)
                kept += 1

    print()
    print("=" * 60)
    print("SOLID-COLOR IMAGE FILTER STATISTICS")
    print("=" * 60)
    print(f"Total entries:          {total}")
    if total > 0:
        print(f"Filtered (solid-color): {solid_count} ({solid_count/total*100:.2f}%)")
        print(f"Errors:                 {error_count} ({error_count/total*100:.2f}%)")
        print(f"Remaining:              {kept} ({kept/total*100:.2f}%)")
    print("=" * 60)
    print(f"\nFiltered JSONL written to: {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
