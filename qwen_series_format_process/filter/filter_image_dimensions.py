#!/usr/bin/env python3
"""
Filter out JSON entries with extreme image dimensions or pure-color images.

Removes samples where the image is:
- Too wide (width above --max-width, default 1280px)
- Too tall (height above --max-height, default 5000px)
- Extreme aspect ratio (max(w,h)/min(w,h) > --max-aspect-ratio, default 10.0)
- Pure color (standard deviation of pixel values below --pure-color-threshold, default 1.0)

Images are read from tar archives using the entry's "shard" and "image_in_tar" fields.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tarfile
from pathlib import Path

import ijson
import numpy as np
from PIL import Image


# --------------- tar-based image helpers ---------------

_current_tar_name = None
_current_tar_handle = None


def _get_tar_handle(tars_dir, shard):
    """Get a tar file handle, caching the currently open one."""
    global _current_tar_name, _current_tar_handle
    if _current_tar_name != shard:
        if _current_tar_handle is not None:
            _current_tar_handle.close()
        tar_path = os.path.join(tars_dir, shard)
        _current_tar_handle = tarfile.open(tar_path, "r")
        _current_tar_name = shard
    return _current_tar_handle


def _close_tar_handle():
    """Close the cached tar handle if open."""
    global _current_tar_name, _current_tar_handle
    if _current_tar_handle is not None:
        _current_tar_handle.close()
        _current_tar_handle = None
        _current_tar_name = None


def get_image_from_tar(tars_dir, shard, image_name):
    """
    Extract image from tar and return the PIL Image object.

    Returns:
        PIL.Image on success, or raises on failure.
    """
    tf = _get_tar_handle(tars_dir, shard)
    member = tf.extractfile(image_name)
    if member is None:
        raise FileNotFoundError(f"{image_name} not found in {shard}")
    data = member.read()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def is_pure_color(img, threshold=1.0):
    """
    Check if an image is (nearly) a single solid color.

    Args:
        img: PIL Image object.
        threshold: Maximum pixel standard deviation to be considered pure color.

    Returns:
        True if the image is pure color.
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    return float(arr.std()) < threshold


# --------------- filtering ---------------


def filter_by_image_dimensions(
    input_path: str,
    output_path: str,
    tars_dir: str,
    max_width: int = 1280,
    max_height: int = 5000,
    max_aspect_ratio: float = 10.0,
    pure_color_threshold: float = 1.0,
    filtered_output_path: str | None = None,
) -> dict:
    """
    Stream JSON entries, keep only those whose image dimensions are within bounds
    and that are not pure-color images.

    Args:
        input_path: Path to the input JSON file.
        output_path: Path to save the filtered JSON file.
        tars_dir: Directory containing tar archives with images.
        max_width: Maximum allowed width in pixels.
        max_height: Maximum allowed height in pixels.
        max_aspect_ratio: Maximum allowed aspect ratio (max(w,h)/min(w,h)).
        pure_color_threshold: Max pixel std-dev to consider an image pure color.
        filtered_output_path: Optional path to write filtered-out entries with reasons.

    Returns:
        Dictionary with filtering statistics.
    """
    print(f"Streaming JSON from: {input_path}")
    print(f"Writing filtered output to: {output_path}")
    if filtered_output_path:
        print(f"Writing filtered-out entries to: {filtered_output_path}")
    print(f"Tars directory: {tars_dir}")
    print(f"Constraints: max_width={max_width}, max_height={max_height}, "
          f"max_aspect_ratio={max_aspect_ratio}, pure_color_threshold={pure_color_threshold}")

    total_count = 0
    kept_count = 0
    filtered_too_large = 0
    filtered_aspect_ratio = 0
    filtered_pure_color = 0
    filtered_error = 0

    filt_out = open(filtered_output_path, "w", encoding="utf-8") if filtered_output_path else None
    if filt_out:
        filt_out.write("[\n")
    filt_out_first = True

    def _write_filtered(entry, reason):
        nonlocal filt_out_first
        if filt_out is None:
            return
        record = {"reason": reason, "entry": entry}
        if not filt_out_first:
            filt_out.write(",\n")
        json.dump(record, filt_out, indent=2, ensure_ascii=False)
        filt_out_first = False

    with open(input_path, "rb") as fin, open(output_path, "w", encoding="utf-8") as fout:
        fout.write("[\n")
        first = True

        for entry in ijson.items(fin, "item"):
            total_count += 1

            if total_count % 1000 == 0:
                print(
                    f"Processing entry {total_count}... "
                    f"(kept: {kept_count}, "
                    f"large: {filtered_too_large}, aspect: {filtered_aspect_ratio}, "
                    f"pure_color: {filtered_pure_color}, error: {filtered_error})"
                )

            entry_id = entry.get("id", total_count - 1)
            shard = entry.get("shard")
            image_in_tar = entry.get("image_in_tar")

            if not shard or not image_in_tar:
                filtered_error += 1
                _write_filtered(entry, "missing shard/image_in_tar")
                continue

            try:
                img = get_image_from_tar(tars_dir, shard, image_in_tar)
                w, h = img.size
            except Exception as e:
                filtered_error += 1
                _write_filtered(entry, f"read error: {e}")
                continue

            # Check maximum width / height
            if w > max_width or h > max_height:
                filtered_too_large += 1
                _write_filtered(entry, f"too large ({w}x{h})")
                continue

            # Check aspect ratio
            short_side = min(w, h)
            long_side = max(w, h)
            aspect = long_side / short_side if short_side > 0 else float("inf")
            if aspect > max_aspect_ratio:
                filtered_aspect_ratio += 1
                _write_filtered(entry, f"extreme aspect ratio ({w}x{h}, ratio={aspect:.2f})")
                continue

            # Check pure color
            if is_pure_color(img, threshold=pure_color_threshold):
                filtered_pure_color += 1
                _write_filtered(entry, f"pure color ({w}x{h})")
                continue

            # Keep entry
            if not first:
                fout.write(",\n")
            json.dump(entry, fout, indent=2, ensure_ascii=False)
            first = False
            kept_count += 1

        fout.write("\n]")

    if filt_out:
        filt_out.write("\n]")
        filt_out.close()

    _close_tar_handle()

    total_filtered = filtered_too_large + filtered_aspect_ratio + filtered_pure_color + filtered_error
    stats = {
        "total_entries": total_count,
        "kept_count": kept_count,
        "filtered_too_large": filtered_too_large,
        "filtered_aspect_ratio": filtered_aspect_ratio,
        "filtered_pure_color": filtered_pure_color,
        "filtered_error": filtered_error,
        "total_filtered": total_filtered,
    }

    print("\n" + "=" * 60)
    print("IMAGE FILTER STATISTICS")
    print("=" * 60)
    print(f"Total entries:               {stats['total_entries']}")
    if total_count > 0:
        print(f"Kept:                        {kept_count} ({kept_count / total_count * 100:.2f}%)")
        print(f"Filtered (too large):        {filtered_too_large} ({filtered_too_large / total_count * 100:.2f}%)")
        print(f"Filtered (aspect ratio):     {filtered_aspect_ratio} ({filtered_aspect_ratio / total_count * 100:.2f}%)")
        print(f"Filtered (pure color):       {filtered_pure_color} ({filtered_pure_color / total_count * 100:.2f}%)")
        print(f"Filtered (read error):       {filtered_error} ({filtered_error / total_count * 100:.2f}%)")
    print("=" * 60)

    if filtered_output_path:
        print(f"\nFiltered-out entries saved to: {filtered_output_path}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter out JSON entries with extreme image dimensions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python filter_image_dimensions.py --tars-dir /path/to/tars --input data.json --output filtered.json
  python filter_image_dimensions.py --max-width 1024 --max-height 4096 --max-aspect-ratio 5.0
        """.strip(),
    )
    parser.add_argument("--input", type=str, required=True, help="Input JSON file path")
    parser.add_argument("--output", type=str, required=True, help="Output JSON file path")
    parser.add_argument("--tars-dir", type=str, required=True, help="Directory containing tar files with images")
    parser.add_argument("--max-width", type=int, default=1280, help="Maximum width in pixels (default: 1280)")
    parser.add_argument("--max-height", type=int, default=5000, help="Maximum height in pixels (default: 5000)")
    parser.add_argument(
        "--max-aspect-ratio",
        type=float,
        default=10.0,
        help="Maximum aspect ratio max(w,h)/min(w,h) (default: 10.0)",
    )
    parser.add_argument(
        "--pure-color-threshold",
        type=float,
        default=1.0,
        help="Max pixel std-dev to consider pure color (default: 1.0)",
    )
    parser.add_argument(
        "--filtered-output",
        type=str,
        default=None,
        help="Optional path to write filtered-out entries with reasons as JSON",
    )

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        return 1

    if not Path(args.tars_dir).is_dir():
        print(f"Error: Tars directory not found: {args.tars_dir}")
        return 1

    try:
        filter_by_image_dimensions(
            input_path=args.input,
            output_path=args.output,
            tars_dir=args.tars_dir,
            max_width=args.max_width,
            max_height=args.max_height,
            max_aspect_ratio=args.max_aspect_ratio,
            pure_color_threshold=args.pure_color_threshold,
            filtered_output_path=args.filtered_output,
        )
        print(f"\nSuccess! Filtered data saved to: {args.output}")
        return 0
    except Exception as e:
        print(f"\nError during filtering: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
