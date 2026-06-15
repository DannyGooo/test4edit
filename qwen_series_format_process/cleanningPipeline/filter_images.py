#!/usr/bin/env python3
"""
Filter JSON entries with corrupted or solid-color images.

Supports two modes:
- File mode: images are loose files on disk (default)
- Tar mode: images are stored inside tar archives (--tars-dir)

Streams JSON entries with ijson, checks each image for:
1. Corruption - can't be opened/decoded by PIL
2. Solid color - standard deviation of pixel values is 0 (or below threshold)
"""

import argparse
import io
import json
import os
import tarfile

import ijson
import numpy as np
from pathlib import Path
from PIL import Image


# Cache for the currently open tar file handle
_current_tar_name = None
_current_tar_handle = None


def _get_tar_handle(tars_dir, shard):
    """Get a tar file handle, caching the currently open one."""
    global _current_tar_name, _current_tar_handle
    if _current_tar_name != shard:
        if _current_tar_handle is not None:
            _current_tar_handle.close()
        tar_path = os.path.join(tars_dir, shard)
        _current_tar_handle = tarfile.open(tar_path, 'r')
        _current_tar_name = shard
    return _current_tar_handle


def _close_tar_handle():
    """Close the cached tar handle if open."""
    global _current_tar_name, _current_tar_handle
    if _current_tar_handle is not None:
        _current_tar_handle.close()
        _current_tar_handle = None
        _current_tar_name = None


def get_image_bytes_from_tar(tars_dir, shard, image_name):
    """Extract image bytes from a tar archive."""
    tf = _get_tar_handle(tars_dir, shard)
    member = tf.extractfile(image_name)
    if member is None:
        raise FileNotFoundError(f"{image_name} not found in {shard}")
    return member.read()


def check_image_bytes(data, std_threshold):
    """
    Check if image bytes represent a valid, non-solid image.

    Returns:
        (keep, reason)
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception as e:
        return False, f"corrupted (verify failed: {e})"

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        return False, f"corrupted (load failed: {e})"

    try:
        arr = np.array(img)
        std = np.std(arr)
        if std <= std_threshold:
            return False, f"solid-color (std={std:.4f})"
    except Exception as e:
        return False, f"corrupted (numpy conversion failed: {e})"

    return True, ""


def check_image_file(image_path, std_threshold):
    """
    Check if an image file is valid and has meaningful content.

    Returns:
        (keep, reason)
    """
    try:
        img = Image.open(image_path)
        img.verify()
    except Exception as e:
        return False, f"corrupted (verify failed: {e})"

    try:
        img = Image.open(image_path)
        img.load()
    except Exception as e:
        return False, f"corrupted (load failed: {e})"

    try:
        arr = np.array(img)
        std = np.std(arr)
        if std <= std_threshold:
            return False, f"solid-color (std={std:.4f})"
    except Exception as e:
        return False, f"corrupted (numpy conversion failed: {e})"

    return True, ""


def filter_images(
    input_path,
    output_path,
    tars_dir=None,
    image_base_dir=None,
    std_threshold=0.0,
):
    """
    Filter JSON entries with bad images using ijson streaming.

    If tars_dir is provided, reads images from tar archives using
    entry["shard"] and entry["image_in_tar"]. Otherwise reads loose
    files using entry["image"] relative to image_base_dir.
    """
    tar_mode = tars_dir is not None

    if not tar_mode:
        if image_base_dir:
            base_dir = Path(image_base_dir)
        else:
            base_dir = Path(input_path).parent

    print(f"Streaming JSON from: {input_path}")
    print(f"Writing filtered output to: {output_path}")
    if tar_mode:
        print(f"Tar mode: reading images from {tars_dir}")
    else:
        print(f"File mode: image base directory: {base_dir}")
    print(f"Std deviation threshold: {std_threshold}")

    total = 0
    kept = 0
    corrupted = 0
    solid_color = 0
    missing = 0

    with open(input_path, 'rb') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:
        fout.write('[\n')
        first = True

        for entry in ijson.items(fin, 'item'):
            total += 1

            if total % 1000 == 0:
                print(f"Processing entry {total}... (kept: {kept}, corrupted: {corrupted}, solid: {solid_color}, missing: {missing})")

            entry_id = entry.get("id", total - 1)

            if tar_mode:
                shard = entry.get("shard")
                image_name = entry.get("image_in_tar")
                if not shard or not image_name:
                    missing += 1
                    print(f"Missing shard/image_in_tar for entry {entry_id}")
                    continue

                try:
                    data = get_image_bytes_from_tar(tars_dir, shard, image_name)
                except Exception as e:
                    missing += 1
                    print(f"Cannot read image for entry {entry_id} ({shard}/{image_name}): {e}")
                    continue

                keep, reason = check_image_bytes(data, std_threshold)
            else:
                image_rel = entry.get("image", "")
                image_path = base_dir / image_rel

                if not image_path.exists():
                    missing += 1
                    print(f"Missing image for entry {entry_id}: {image_path}")
                    continue

                keep, reason = check_image_file(image_path, std_threshold)

            if keep:
                if not first:
                    fout.write(',\n')
                json.dump(entry, fout, indent=2, ensure_ascii=False)
                first = False
                kept += 1
            else:
                if "corrupted" in reason:
                    corrupted += 1
                else:
                    solid_color += 1
                print(f"Filtered out entry {entry_id}: {reason}")

        fout.write('\n]')

    _close_tar_handle()

    print("\n" + "=" * 60)
    print("IMAGE FILTERING STATISTICS")
    print("=" * 60)
    print(f"Total entries:        {total}")
    if total > 0:
        print(f"Kept:                 {kept} ({kept/total*100:.2f}%)")
        print(f"Corrupted:            {corrupted} ({corrupted/total*100:.2f}%)")
        print(f"Solid-color:          {solid_color} ({solid_color/total*100:.2f}%)")
        print(f"Missing:              {missing} ({missing/total*100:.2f}%)")
    else:
        print("Kept:                 0")
        print("Corrupted:            0")
        print("Solid-color:          0")
        print("Missing:              0")
    print("=" * 60)

    return {
        "total": total,
        "kept": kept,
        "corrupted": corrupted,
        "solid_color": solid_color,
        "missing": missing,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Filter JSON entries with corrupted or solid-color images",
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input JSON file path",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--tars-dir",
        type=str,
        default=None,
        help="Directory containing images-*.tar files (tar mode)",
    )
    parser.add_argument(
        "--image-base-dir",
        type=str,
        default=None,
        help="Base directory for resolving image paths (file mode, default: input JSON's parent dir)",
    )
    parser.add_argument(
        "--std-threshold",
        type=float,
        default=0.0,
        help="Standard deviation threshold for solid-color detection (default: 0.0)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers (default: 1, for future use)",
    )

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        return 1

    if args.tars_dir and not Path(args.tars_dir).is_dir():
        print(f"Error: Tars directory not found: {args.tars_dir}")
        return 1

    try:
        filter_images(
            args.input,
            args.output,
            tars_dir=args.tars_dir,
            image_base_dir=args.image_base_dir,
            std_threshold=args.std_threshold,
        )
        print(f"\nSuccess! Filtered data saved to: {args.output}")
        return 0
    except Exception as e:
        print(f"\nError during filtering: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
