#!/usr/bin/env python3
"""
Transform dataset from JSON format to code-anything directory structure.

Input: JSON file with entries containing id, image path, and conversations
Output:
  - {outputDir}/code-anything/3_web/images/web_XXXXXX.png
  - {outputDir}/code-anything/3_web/codes/web_XXXXXX.html
  - {outputDir}/code-anything/3_web/meta_data_web.json
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Transform dataset to code-anything format"
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        required=True,
        help="Path to input JSON file",
    )
    parser.add_argument(
        "--input-image-dir",
        type=str,
        default=None,
        help="Base directory for input images (if not specified, uses parent directory of input JSON)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output directory (will create code-anything/3_web subdirectories)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Limit the number of entries to process (for testing)",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Starting index for output file naming (default: 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without actually copying files",
    )
    return parser.parse_args()


def extract_html_from_entry(entry):
    """Extract HTML code from the conversations field."""
    try:
        conversations = entry.get("conversations", [])
        for conv in conversations:
            if conv.get("from") == "gpt":
                return conv.get("value", "")
    except (KeyError, IndexError, TypeError) as e:
        print(f"Warning: Could not extract HTML from entry {entry.get('id', 'unknown')}: {e}")
    return None


def main():
    args = parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    # Determine input image directory
    if args.input_image_dir:
        input_image_dir = Path(args.input_image_dir)
    else:
        input_image_dir = input_path.parent

    # Create output directories
    web_dir = output_dir / "code-anything" / "3_web"
    images_dir = web_dir / "images"
    codes_dir = web_dir / "codes"

    if not args.dry_run:
        images_dir.mkdir(parents=True, exist_ok=True)
        codes_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input JSON: {input_path}")
    print(f"Input image directory: {input_image_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Images will be saved to: {images_dir}")
    print(f"HTML codes will be saved to: {codes_dir}")

    # Read input JSON file in streaming manner for large files
    print("\nReading input JSON file...")

    # For very large JSON files, we need to read it in a memory-efficient way
    # First, let's check the file size
    file_size = input_path.stat().st_size
    print(f"Input file size: {file_size / (1024*1024):.2f} MB")

    # Read the JSON file
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: Expected JSON array at root level")
        sys.exit(1)

    total_entries = len(data)
    print(f"Total entries in JSON: {total_entries}")

    # Apply limit if specified
    if args.limit:
        data = data[:args.limit]
        print(f"Processing limited to first {args.limit} entries")

    # Process entries
    metadata = []
    processed = 0
    skipped = 0

    for idx, entry in enumerate(data):
        output_idx = args.start_index + idx
        output_name = f"web_{output_idx:06d}"

        # Extract HTML code
        html_code = extract_html_from_entry(entry)
        if html_code is None:
            print(f"Warning: Skipping entry {entry.get('id', 'unknown')} - no HTML found")
            skipped += 1
            continue

        # Get source image path
        source_image_rel = entry.get("image", "")
        source_image_path = input_image_dir / source_image_rel

        # Output paths
        output_image_path = images_dir / f"{output_name}.png"
        output_html_path = codes_dir / f"{output_name}.html"

        # Relative path for metadata (from output root)
        metadata_image_path = f"code-anything/3_web/images/{output_name}.png"

        if args.dry_run:
            print(f"[DRY RUN] Would process entry {idx}:")
            print(f"  Source image: {source_image_path}")
            print(f"  Output image: {output_image_path}")
            print(f"  Output HTML: {output_html_path}")
        else:
            # Copy image if it exists
            if source_image_path.exists():
                shutil.copy2(source_image_path, output_image_path)
            else:
                print(f"Warning: Source image not found: {source_image_path}")
                skipped += 1
                continue

            # Write HTML file (raw HTML without markdown fences)
            with open(output_html_path, "w", encoding="utf-8") as f:
                f.write(html_code)

        # Create metadata entry with markdown fenced code
        metadata_entry = {
            "source": "htmlSlicer screenshot database",
            "code_language": "html",
            "image_path": metadata_image_path,
            "code": f"```html\n{html_code}\n```"
        }
        metadata.append(metadata_entry)
        processed += 1

        # Progress update every 1000 entries
        if (idx + 1) % 1000 == 0:
            print(f"Processed {idx + 1}/{len(data)} entries...")

    # Write metadata JSON
    metadata_path = web_dir / "meta_data_web.json"
    if not args.dry_run:
        print(f"\nWriting metadata to: {metadata_path}")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"Processing complete!")
    print(f"  Total entries in input: {total_entries}")
    print(f"  Entries processed: {processed}")
    print(f"  Entries skipped: {skipped}")
    if not args.dry_run:
        print(f"  Images saved to: {images_dir}")
        print(f"  HTML files saved to: {codes_dir}")
        print(f"  Metadata saved to: {metadata_path}")


if __name__ == "__main__":
    main()
