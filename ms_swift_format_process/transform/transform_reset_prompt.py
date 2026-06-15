#!/usr/bin/env python3
"""Reset the user-role prompt in every sample of an ms_swift JSONL file.

Each input line carries a `messages` array. This script overwrites the `content`
of the FIRST `role == "user"` message with the constant `NEW_PROMPT_TEXT` below,
verbatim. All other fields (assistant content, images, extra keys) are preserved.

Edit `NEW_PROMPT_TEXT` to change the replacement prompt. The caller is
responsible for including any required multimodal token (e.g. `<image>`).
"""

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Replacement prompt — edit this string to change the prompt written into every
# sample's first user message. Verbatim replacement: include `<image>` yourself
# if the dataset is multimodal.
# ---------------------------------------------------------------------------
NEW_PROMPT_TEXT = """<image>
Drawing from the webpage screenshot, create corresponding HTML and CSS code.
"""


def reset_prompt_in_entry(entry):
    """Overwrite the first user-role message's content with NEW_PROMPT_TEXT.

    Returns (modified_entry, ok, reason). `ok=False` means no user message was
    found and the entry is returned unchanged.
    """
    messages = entry.get("messages")
    if not isinstance(messages, list):
        return entry, False, "no_messages_field"

    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            msg["content"] = NEW_PROMPT_TEXT
            return entry, True, None

    return entry, False, "no_user_message"


def transform_jsonl_reset_prompt(input_path, output_path, num_samples=0):
    total_entries = 0
    with open(input_path, "r", encoding="utf-8") as f:
        for _ in f:
            total_entries += 1

    if num_samples > 0:
        total_entries = min(total_entries, num_samples)
    print(f"Processing {total_entries} entries to reset user prompt...")

    entries_processed = 0
    entries_skipped = 0
    skip_reasons = {}
    skipped_entries = []

    with open(input_path, "r", encoding="utf-8") as fin, \
         open(output_path, "w", encoding="utf-8") as fout:

        for i, line in enumerate(fin):
            if num_samples > 0 and i >= num_samples:
                break

            line = line.strip()
            if not line:
                continue

            if (i + 1) % 100 == 0:
                print(f"Progress: {i + 1}/{total_entries} entries processed")

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                entries_skipped += 1
                reason = "invalid_json"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    "line": i + 1,
                    "reason": reason,
                    "message": f"Invalid JSON: {str(e)}",
                })
                continue

            entry_id = entry.get("id", f"line_{i}")
            new_entry, ok, reason = reset_prompt_in_entry(entry)

            fout.write(json.dumps(new_entry, ensure_ascii=False) + "\n")

            if ok:
                entries_processed += 1
            else:
                entries_skipped += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    "id": entry_id,
                    "reason": reason,
                    "message": f"Entry written through unchanged: {reason}",
                })

    skip_report_path = output_path + ".skipped.json"
    if entries_skipped > 0:
        skip_report = {
            "total_skipped": entries_skipped,
            "skip_reasons": skip_reasons,
            "skipped_entries": skipped_entries,
        }
        with open(skip_report_path, "w", encoding="utf-8") as f:
            json.dump(skip_report, f, ensure_ascii=False, indent=2)

    return {
        "total_entries": total_entries,
        "entries_processed": entries_processed,
        "entries_skipped": entries_skipped,
        "skip_reasons": skip_reasons,
        "input_path": input_path,
        "output_path": output_path,
        "skip_report_path": skip_report_path if entries_skipped > 0 else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reset the user-role prompt in every sample of an ms_swift JSONL file"
    )
    parser.add_argument("--input", type=str, required=True, help="Input JSONL file path")
    parser.add_argument("--output", type=str, required=True, help="Output JSONL file path")
    parser.add_argument(
        "--num_samples",
        type=int,
        default=0,
        help="Number of samples to process (0 = all)",
    )

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        stats = transform_jsonl_reset_prompt(args.input, args.output, args.num_samples)

        print("\n" + "=" * 60)
        print("RESET PROMPT SUMMARY")
        print("=" * 60)
        print(f"Total entries:          {stats['total_entries']}")
        print(f"Entries processed:      {stats['entries_processed']}")
        print(f"Entries skipped:        {stats['entries_skipped']}")

        if stats["entries_skipped"] > 0 and stats.get("skip_reasons"):
            print("\nSkip Reason Breakdown:")
            for reason, count in sorted(
                stats["skip_reasons"].items(), key=lambda x: x[1], reverse=True
            ):
                reason_display = reason.replace("_", " ").title()
                print(f"  - {reason_display}: {count}")

        print(f"\nOutput written to:      {stats['output_path']}")
        if stats.get("skip_report_path"):
            print(f"Skip report written to: {stats['skip_report_path']}")

        print("=" * 60)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
