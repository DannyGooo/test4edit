#!/usr/bin/env python3
"""
Transform ms_swift JSONL format to qwen_series JSON format.

Input format (JSONL, one JSON per line):
  {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Output format (JSON array):
  [
    {
      "id": "chunk_0_row_0",
      "shard": "images-00000.tar",
      "image_in_tar": "chunk_0_row_0.png",
      "conversations": [
        {"from": "human", "value": "..."},
        {"from": "gpt", "value": "..."}
      ]
    },
    ...
  ]

Usage:
  python3 transform_to_qwen_series.py --input data.jsonl --output data.json [--num_samples N]
"""

import json
import argparse
import sys
from pathlib import Path

ROLE_MAP = {
    "user": "human",
    "assistant": "gpt",
    "system": "system",
}


def parse_image_path(image_path: str):
    """
    Parse 'images-00000.tar/chunk_0_row_0.png' into shard, image_in_tar, and id.
    """
    parts = image_path.split("/", 1)
    if len(parts) == 2:
        shard = parts[0]
        image_in_tar = parts[1]
    else:
        shard = ""
        image_in_tar = image_path

    # id is image filename without extension
    entry_id = Path(image_in_tar).stem
    return shard, image_in_tar, entry_id


def transform_entry(entry: dict) -> dict:
    """Transform a single ms_swift entry to qwen_series format."""
    # Parse image info
    images = entry.get("images", [])
    if images:
        shard, image_in_tar, entry_id = parse_image_path(images[0])
    else:
        shard = ""
        image_in_tar = ""
        entry_id = ""

    # Transform messages to conversations
    conversations = []
    for msg in entry.get("messages", []):
        role = msg.get("role", "")
        mapped_role = ROLE_MAP.get(role, role)
        conversations.append({
            "from": mapped_role,
            "value": msg.get("content", ""),
        })

    return {
        "id": entry_id,
        "shard": shard,
        "image_in_tar": image_in_tar,
        "conversations": conversations,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL to qwen_series JSON format"
    )
    parser.add_argument("--input", type=str, required=True, help="Input JSONL file path")
    parser.add_argument("--output", type=str, required=True, help="Output JSON file path")
    parser.add_argument("--num_samples", type=int, default=0, help="Number of samples to process (0 = all)")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    results = []
    processed = 0
    skipped = 0

    with open(args.input, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if args.num_samples > 0 and processed >= args.num_samples:
                break

            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping line {i + 1} (invalid JSON): {e}", file=sys.stderr)
                skipped += 1
                continue

            results.append(transform_entry(entry))
            processed += 1

            if processed % 1000 == 0:
                print(f"Progress: {processed} entries processed")

    # Write output as formatted JSON array (matching target format)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {processed} entries transformed, {skipped} skipped")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
