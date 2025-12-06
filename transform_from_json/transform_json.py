#!/usr/bin/env python3
"""
Filter JSON entries where GPT response token count exceeds 8000 tokens.
Supports both tiktoken (OpenAI) and Qwen2.5-VL tokenizers.

jq 'length' /home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_filtered.json
"""

import argparse
import json
import tiktoken
from pathlib import Path
from typing import Union, Any


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
    max_tokens: int = 8000
) -> dict:
    """
    Filter JSON entries where GPT response exceeds max_tokens.

    Args:
        input_path: Path to the input JSON file
        output_path: Path to save the filtered JSON file
        tokenizer: The tokenizer object
        tokenizer_type: Type of tokenizer ("tiktoken" or "qwen")
        max_tokens: Maximum token count threshold (default: 8000)

    Returns:
        Dictionary with statistics about the filtering
    """
    print(f"Loading JSON from: {input_path}")

    # Load the input JSON
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected JSON to be an array of objects")

    total_entries = len(data)
    print(f"Total entries loaded: {total_entries}")

    # Filter entries
    filtered_data = []
    filtered_out = []

    for i, entry in enumerate(data):
        if (i + 1) % 100 == 0:
            print(f"Processing entry {i + 1}/{total_entries}...")

        gpt_response = get_gpt_response_from_entry(entry)

        if gpt_response is None:
            print(f"Warning: No GPT response found in entry {entry.get('id', i)}")
            filtered_data.append(entry)
            continue

        token_count = count_tokens(gpt_response, tokenizer, tokenizer_type)

        if token_count <= max_tokens:
            filtered_data.append(entry)
        else:
            filtered_out.append({
                "id": entry.get("id", i),
                "token_count": token_count
            })
            print(f"Filtered out entry {entry.get('id', i)}: {token_count} tokens")

    # Save the filtered data
    print(f"\nSaving filtered data to: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_data, f, indent=2, ensure_ascii=False)

    # Statistics
    stats = {
        "total_entries": total_entries,
        "filtered_out_count": len(filtered_out),
        "remaining_count": len(filtered_data),
        "filtered_out_entries": filtered_out
    }

    print("\n" + "="*60)
    print("FILTERING STATISTICS")
    print("="*60)
    print(f"Total entries:        {stats['total_entries']}")
    print(f"Filtered out:         {stats['filtered_out_count']} ({stats['filtered_out_count']/stats['total_entries']*100:.2f}%)")
    print(f"Remaining:            {stats['remaining_count']} ({stats['remaining_count']/stats['total_entries']*100:.2f}%)")
    print("="*60)

    if filtered_out:
        print("\nFiltered out entries (ID and token count):")
        for item in filtered_out[:10]:  # Show first 10
            print(f"  - {item['id']}: {item['token_count']} tokens")
        if len(filtered_out) > 10:
            print(f"  ... and {len(filtered_out) - 10} more")

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
            args.max_tokens
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
