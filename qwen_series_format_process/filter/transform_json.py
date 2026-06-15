#!/usr/bin/env python3
"""
Filter JSON entries where GPT response token count exceeds 8000 tokens.
Supports both tiktoken (OpenAI) and Qwen2.5-VL tokenizers.

jq 'length' /home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_filtered.json
"""

import argparse
import io
import json
import os
import tarfile

import ijson
import tiktoken
from PIL import Image
from pathlib import Path
from typing import Union, Any


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


def check_image_corrupted(data):
    """
    Check if image data is corrupted.

    Returns:
        (is_corrupted: bool, reason: str)
    """
    if len(data) == 0:
        return True, "empty data"
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except (OSError, SyntaxError, Image.UnidentifiedImageError, Exception) as e:
        return True, str(e)
    return False, ""


def load_tokenizer(tokenizer_type: str) -> tuple[Any, str]:
    """
    Load the appropriate tokenizer based on type.

    Args:
        tokenizer_type: Either "tiktoken" or "qwen"

    Returns:
        Tuple of (tokenizer, tokenizer_type)
    """
    if tokenizer_type == "tiktoken":
        print("Using tiktoken (cl100k_base) tokenizer")
        return tiktoken.get_encoding("cl100k_base"), "tiktoken"
    elif tokenizer_type == "qwen":
        from transformers import AutoTokenizer
        print("Loading Qwen2.5-VL tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen2-VL-7B-Instruct",
            trust_remote_code=True
        )
        print("Qwen2.5-VL tokenizer loaded successfully")
        return tokenizer, "qwen"
    else:
        raise ValueError(f"Unknown tokenizer type: {tokenizer_type}")


def count_tokens(text: str, tokenizer: Any, tokenizer_type: str) -> int:
    """
    Count the number of tokens in a text string using the provided tokenizer.

    Args:
        text: The text to count tokens for
        tokenizer: The tokenizer object
        tokenizer_type: Type of tokenizer ("tiktoken" or "qwen")

    Returns:
        The number of tokens
    """
    if tokenizer_type == "tiktoken":
        return len(tokenizer.encode(text))
    elif tokenizer_type == "qwen":
        return len(tokenizer.encode(text, add_special_tokens=False))
    else:
        raise ValueError(f"Unknown tokenizer type: {tokenizer_type}")


def get_gpt_response_from_entry(entry: dict) -> str | None:
    """
    Extract the GPT response value from a conversation entry.

    Args:
        entry: A single entry from the JSON array

    Returns:
        The GPT response value, or None if not found
    """
    conversations = entry.get("conversations", [])
    for conv in conversations:
        if conv.get("from") == "gpt":
            return conv.get("value")
    return None


def filter_json_by_token_count(
    input_path: str,
    output_path: str,
    tokenizer: Any,
    tokenizer_type: str,
    max_tokens: int = 8000,
    tars_dir: str = None
) -> dict:
    """
    Filter JSON entries where GPT response exceeds max_tokens.

    Uses ijson to stream entries one at a time and writes output incrementally,
    avoiding loading the entire JSON array into memory.

    Args:
        input_path: Path to the input JSON file
        output_path: Path to save the filtered JSON file
        tokenizer: The tokenizer object
        tokenizer_type: Type of tokenizer ("tiktoken" or "qwen")
        max_tokens: Maximum token count threshold (default: 8000)
        tars_dir: Directory containing tar files for image corruption checking (optional)

    Returns:
        Dictionary with statistics about the filtering
    """
    print(f"Streaming JSON from: {input_path}")
    print(f"Writing filtered output to: {output_path}")
    if tars_dir:
        print(f"Image corruption check enabled (tars dir: {tars_dir})")

    total_count = 0
    kept_count = 0
    filtered_out_count = 0
    corrupted_count = 0
    # Keep only the last N filtered-out entries for the summary
    recent_filtered_out = []
    MAX_RECENT = 10

    with open(input_path, 'rb') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:
        fout.write('[\n')
        first = True

        for entry in ijson.items(fin, 'item'):
            total_count += 1

            if total_count % 1000 == 0:
                print(f"Processing entry {total_count}... (kept: {kept_count}, filtered: {filtered_out_count}, corrupted: {corrupted_count})")

            # Image corruption check
            if tars_dir is not None:
                shard = entry.get("shard")
                image_in_tar = entry.get("image_in_tar")
                entry_id = entry.get("id", total_count - 1)
                if not shard or not image_in_tar:
                    print(f"Corrupted (missing shard/image_in_tar) entry {entry_id}")
                    corrupted_count += 1
                    continue
                try:
                    data = get_image_bytes_from_tar(tars_dir, shard, image_in_tar)
                    is_corrupted, reason = check_image_corrupted(data)
                    if is_corrupted:
                        print(f"Corrupted image in entry {entry_id}: {reason}")
                        corrupted_count += 1
                        continue
                except Exception as e:
                    print(f"Corrupted (extraction error) entry {entry_id}: {e}")
                    corrupted_count += 1
                    continue

            gpt_response = get_gpt_response_from_entry(entry)

            if gpt_response is None:
                print(f"Warning: No GPT response found in entry {entry.get('id', total_count - 1)}")
                # Keep entries with no GPT response
                if not first:
                    fout.write(',\n')
                json.dump(entry, fout, indent=2, ensure_ascii=False)
                first = False
                kept_count += 1
                continue

            token_count = count_tokens(gpt_response, tokenizer, tokenizer_type)

            if token_count <= max_tokens:
                if not first:
                    fout.write(',\n')
                json.dump(entry, fout, indent=2, ensure_ascii=False)
                first = False
                kept_count += 1
            else:
                filtered_out_count += 1
                entry_id = entry.get("id", total_count - 1)
                print(f"Filtered out entry {entry_id}: {token_count} tokens")
                if len(recent_filtered_out) < MAX_RECENT:
                    recent_filtered_out.append({
                        "id": entry_id,
                        "token_count": token_count
                    })

        fout.write('\n]')

    if tars_dir is not None:
        _close_tar_handle()

    # Statistics
    stats = {
        "total_entries": total_count,
        "filtered_out_count": filtered_out_count,
        "corrupted_count": corrupted_count,
        "remaining_count": kept_count,
    }

    print("\n" + "="*60)
    print("FILTERING STATISTICS")
    print("="*60)
    print(f"Total entries:        {stats['total_entries']}")
    if total_count > 0:
        print(f"Filtered (tokens):    {stats['filtered_out_count']} ({stats['filtered_out_count']/stats['total_entries']*100:.2f}%)")
        print(f"Filtered (corrupted): {stats['corrupted_count']} ({stats['corrupted_count']/stats['total_entries']*100:.2f}%)")
        print(f"Remaining:            {stats['remaining_count']} ({stats['remaining_count']/stats['total_entries']*100:.2f}%)")
    else:
        print("Filtered (tokens):    0")
        print("Filtered (corrupted): 0")
        print("Remaining:            0")
    print("="*60)

    if recent_filtered_out:
        print("\nFiltered out entries (ID and token count):")
        for item in recent_filtered_out:
            print(f"  - {item['id']}: {item['token_count']} tokens")
        if filtered_out_count > MAX_RECENT:
            print(f"  ... and {filtered_out_count - MAX_RECENT} more")

    return stats


def main():
    """Main function to run the filtering."""
    parser = argparse.ArgumentParser(
        description="Filter JSON entries where GPT response exceeds token limit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using tiktoken (default)
  python transform_json.py --tokenizer tiktoken

  # Using Qwen2.5-VL tokenizer
  python transform_json.py --tokenizer qwen

  # Custom paths and token limit
  python transform_json.py --tokenizer qwen --max-tokens 10000 \\
    --input /path/to/input.json --output /path/to/output.json
        """
    )
    parser.add_argument(
        "--tokenizer",
        type=str,
        choices=["tiktoken", "qwen"],
        default="tiktoken",
        help="Tokenizer to use: 'tiktoken' (OpenAI GPT) or 'qwen' (Qwen2.5-VL). Default: tiktoken"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Maximum token count threshold. Entries exceeding this will be filtered out. Default: 8000"
    )
    parser.add_argument(
        "--input",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json",
        help="Input JSON file path"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_filtered.json",
        help="Output JSON file path"
    )
    parser.add_argument(
        "--tars-dir",
        type=str,
        default=None,
        help="Directory containing tar files for image corruption checking. When provided, entries with corrupted images are filtered out."
    )

    args = parser.parse_args()

    # Verify input file exists
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        return 1

    try:
        # Load tokenizer
        tokenizer, tokenizer_type = load_tokenizer(args.tokenizer)
        print()

        # Run filtering
        stats = filter_json_by_token_count(
            args.input,
            args.output,
            tokenizer,
            tokenizer_type,
            args.max_tokens,
            tars_dir=args.tars_dir
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
