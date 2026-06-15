#!/usr/bin/env python3
"""Filter JSONL entries whose image dimensions exceed a threshold.

Images are stored inside tar files. The JSONL references paths like
"images-00000.tar/chunk_0_row_0.png", meaning the file chunk_0_row_0.png
inside the tar archive images-00000.tar.
"""

import argparse
import io
import json
import tarfile
from pathlib import Path

from PIL import Image

# Allow very large images without warnings — we filter by dimensions ourselves.
Image.MAX_IMAGE_PIXELS = None


def build_tar_index(image_root):
    """Build an index: tar_name -> {member_name: TarInfo} for all tar files."""
    index = {}
    tar_handles = {}
    for tar_path in sorted(Path(image_root).glob("images-*.tar")):
        tar_name = tar_path.name
        print(f"  Indexing {tar_name}...")
        tf = tarfile.open(tar_path, "r")
        members = {}
        for m in tf.getmembers():
            if m.isfile():
                clean_name = m.name.lstrip("./")
                members[clean_name] = m
        index[tar_name] = members
        tar_handles[tar_name] = tf
    return index, tar_handles


def get_image_size(tar_handles, tar_index, img_rel):
    """Get the (width, height) of an image inside a tar archive.

    Returns:
        ((width, height), None) on success, or (None, error_string) on failure.
    """
    parts = img_rel.split("/", 1)
    if len(parts) != 2:
        return None, f"{img_rel} (bad path format)"

    tar_name, member_name = parts

    if tar_name not in tar_index:
        return None, f"{img_rel} (tar not found)"

    if member_name not in tar_index[tar_name]:
        return None, f"{img_rel} (missing in tar)"

    try:
        tf = tar_handles[tar_name]
        member = tar_index[tar_name][member_name]
        f = tf.extractfile(member)
        if f is None:
            return None, f"{img_rel} (not a file)"
        data = f.read()
        img = Image.open(io.BytesIO(data))
        return img.size, None
    except Exception as e:
        return None, f"{img_rel} ({e})"


def main():
    parser = argparse.ArgumentParser(
        description="Filter JSONL entries whose image dimensions exceed a threshold",
    )
    parser.add_argument("--jsonl", required=True, help="Path to input JSONL file")
    parser.add_argument("--image_root", required=True,
                        help="Directory containing tar files")
    parser.add_argument("--output", default=None,
                        help="Output JSONL path (default: <input>_dim_filtered.jsonl)")
    parser.add_argument("--max-height", type=int, default=10000,
                        help="Maximum image height in pixels (default: 10000)")
    parser.add_argument("--max-width", type=int, default=None,
                        help="Maximum image width in pixels (default: no limit)")
    args = parser.parse_args()

    if args.output is None:
        p = Path(args.jsonl)
        args.output = str(p.with_stem(p.stem + "_dim_filtered"))

    if not Path(args.jsonl).exists():
        print(f"Error: Input file not found: {args.jsonl}")
        return 1

    with open(args.jsonl) as f:
        lines = f.readlines()

    total = len(lines)
    print(f"Total entries: {total}")
    print(f"Max height: {args.max_height}px")
    print(f"Max width:  {args.max_width}px" if args.max_width else "Max width:  (no limit)")
    print("Building tar index...")
    tar_index, tar_handles = build_tar_index(args.image_root)
    print(f"Indexed {len(tar_index)} tar files.\n")

    print("Scanning images...")
    valid_lines = []
    filtered_height = 0
    filtered_width = 0
    error_count = 0

    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            error_count += 1
            print(f"  [BAD] line {i}: invalid JSON")
            continue

        rejected = False
        for img_rel in entry.get("images", []):
            size, err = get_image_size(tar_handles, tar_index, img_rel)
            if err:
                print(f"  [ERROR] line {i}: {err}")
                rejected = True
                error_count += 1
                break
            width, height = size
            if height > args.max_height:
                entry_id = entry.get("id", i)
                print(f"  [FILTERED] line {i} (id={entry_id}): height={height}px")
                rejected = True
                filtered_height += 1
                break
            if args.max_width is not None and width > args.max_width:
                entry_id = entry.get("id", i)
                print(f"  [FILTERED] line {i} (id={entry_id}): width={width}px")
                rejected = True
                filtered_width += 1
                break

        if not rejected:
            valid_lines.append(line)

        if (i + 1) % 5000 == 0:
            print(f"  Checked {i + 1}/{total}... "
                  f"(height: {filtered_height}, width: {filtered_width}, errors: {error_count})")

    for tf in tar_handles.values():
        tf.close()

    with open(args.output, "w") as f:
        f.writelines(valid_lines)

    kept = len(valid_lines)
    filtered_total = filtered_height + filtered_width
    print()
    print("=" * 60)
    print("IMAGE DIMENSION FILTER STATISTICS")
    print("=" * 60)
    print(f"Total entries:          {total}")
    if total > 0:
        print(f"Filtered (too tall):    {filtered_height} ({filtered_height / total * 100:.2f}%)")
        print(f"Filtered (too wide):    {filtered_width} ({filtered_width / total * 100:.2f}%)")
        print(f"Filtered (total):       {filtered_total} ({filtered_total / total * 100:.2f}%)")
        print(f"Errors:                 {error_count} ({error_count / total * 100:.2f}%)")
        print(f"Remaining:              {kept} ({kept / total * 100:.2f}%)")
    print("=" * 60)
    print(f"\nFiltered JSONL written to: {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
