#!/usr/bin/env python3
"""
Extract the first N samples from a ms_swift JSONL file into a new JSONL file.

Streams the input line-by-line and writes the original line text unchanged,
so the output is byte-identical to the first N valid JSONL lines of the input.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict


def split_first_n(input_path: str, output_path: str, num_samples: int) -> Dict:
    if num_samples <= 0:
        raise ValueError(f"--num_samples must be > 0, got {num_samples}")

    written = 0
    blank_skipped = 0
    invalid_skipped = 0
    lines_read = 0

    with open(input_path, 'r', encoding='utf-8') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:

        for raw in fin:
            if written >= num_samples:
                break
            lines_read += 1

            stripped = raw.strip()
            if not stripped:
                blank_skipped += 1
                continue

            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                invalid_skipped += 1
                continue

            if not raw.endswith('\n'):
                raw = raw + '\n'
            fout.write(raw)
            written += 1

            if written % 100 == 0:
                print(f"Progress: {written}/{num_samples} samples written")

    return {
        'input_path': input_path,
        'output_path': output_path,
        'samples_requested': num_samples,
        'samples_written': written,
        'blank_lines_skipped': blank_skipped,
        'invalid_json_skipped': invalid_skipped,
        'lines_read': lines_read,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Extract the first N samples from a ms_swift JSONL file.'
    )
    parser.add_argument('--input', type=str, required=True, help='Input JSONL file path')
    parser.add_argument('--output', type=str, required=True, help='Output JSONL file path')
    parser.add_argument('--num_samples', type=int, required=True,
                        help='Number of samples to extract (must be > 0)')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    if args.num_samples <= 0:
        print(f"Error: --num_samples must be > 0, got {args.num_samples}", file=sys.stderr)
        sys.exit(1)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    try:
        stats = split_first_n(args.input, args.output, args.num_samples)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SPLIT FIRST-N SUMMARY")
    print("=" * 60)
    print(f"Input path:            {stats['input_path']}")
    print(f"Output path:           {stats['output_path']}")
    print(f"Samples requested:     {stats['samples_requested']}")
    print(f"Samples written:       {stats['samples_written']}")
    print(f"Lines read:            {stats['lines_read']}")
    print(f"Blank lines skipped:   {stats['blank_lines_skipped']}")
    print(f"Invalid JSON skipped:  {stats['invalid_json_skipped']}")
    print("=" * 60)

    if stats['samples_written'] < stats['samples_requested']:
        print(
            f"Warning: only {stats['samples_written']} valid samples were written "
            f"(requested {stats['samples_requested']}). Input may be shorter than N.",
            file=sys.stderr,
        )


if __name__ == '__main__':
    main()
