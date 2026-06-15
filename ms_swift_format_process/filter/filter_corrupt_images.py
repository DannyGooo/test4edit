#!/usr/bin/env python3
"""Scan a JSONL dataset for corrupt images and produce a cleaned version.

Images are stored inside tar files. The JSONL references paths like
"images-00000.tar/chunk_0_row_0.png", meaning the file chunk_0_row_0.png
inside the tar archive images-00000.tar.
"""

import argparse
import io
import json
import os
import tarfile
from pathlib import Path

from PIL import Image


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
                # Strip leading "./" prefix from tar member names
                clean_name = m.name.lstrip("./")
                members[clean_name] = m
        index[tar_name] = members
        tar_handles[tar_name] = tf
    return index, tar_handles


def check_image_in_tar(tar_handles, tar_index, img_rel):
    """Check if a single image is valid inside a tar. Returns None if OK, error string if bad."""
    # img_rel is like "images-00000.tar/chunk_0_row_0.png"
    parts = img_rel.split("/", 1)
    if len(parts) != 2:
        return f"{img_rel} (bad path format)"

    tar_name, member_name = parts

    if tar_name not in tar_index:
        return f"{img_rel} (tar not found)"

    if member_name not in tar_index[tar_name]:
        return f"{img_rel} (missing in tar)"

    try:
        tf = tar_handles[tar_name]
        member = tar_index[tar_name][member_name]
        f = tf.extractfile(member)
        if f is None:
            return f"{img_rel} (not a file)"
        data = f.read()
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception as e:
        return f"{img_rel} ({e})"

    return None


def main():
    parser = argparse.ArgumentParser(description="Filter corrupt images from JSONL dataset")
    parser.add_argument("--jsonl", required=True, help="Path to input JSONL file")
    parser.add_argument("--image_root", required=True, help="Directory containing tar files")
    parser.add_argument("--output", default=None, help="Output JSONL path (default: <input>_clean.jsonl)")
    args = parser.parse_args()

    if args.output is None:
        p = Path(args.jsonl)
        args.output = str(p.with_stem(p.stem + "_clean"))

    with open(args.jsonl) as f:
        lines = f.readlines()

    print(f"Total entries: {len(lines)}")
    print("Building tar index...")
    tar_index, tar_handles = build_tar_index(args.image_root)
    print(f"Indexed {len(tar_index)} tar files.\n")

    print("Scanning images...")
    valid_lines = []
    corrupt_count = 0

    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            corrupt_count += 1
            print(f"  [BAD] line {i}: invalid JSON")
            continue

        bad = False
        for img_rel in entry.get("images", []):
            err = check_image_in_tar(tar_handles, tar_index, img_rel)
            if err:
                print(f"  [BAD] line {i}: {err}")
                bad = True
                break

        if bad:
            corrupt_count += 1
        else:
            valid_lines.append(line)

        if (i + 1) % 5000 == 0:
            print(f"  Checked {i + 1}/{len(lines)}... (corrupt so far: {corrupt_count})")

    # Close tar handles
    for tf in tar_handles.values():
        tf.close()

    with open(args.output, "w") as f:
        f.writelines(valid_lines)

    print(f"\nDone. Total: {len(lines)}, Valid: {len(valid_lines)}, Corrupt: {corrupt_count}")
    print(f"Cleaned JSONL written to: {args.output}")


if __name__ == "__main__":
    main()
