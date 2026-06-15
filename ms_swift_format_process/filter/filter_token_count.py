#!/usr/bin/env python3
"""Filter JSONL entries where the assistant/GPT response exceeds a token limit.

Reads a JSONL dataset line-by-line, counts tokens in the assistant response,
and writes a cleaned version with only entries under the threshold.

Supports both conversation formats:
  - {"from": "gpt", "value": "..."} (conversations style)
  - {"role": "assistant", "content": "..."} (messages style)
"""

import argparse
import json
from pathlib import Path


def load_tokenizer(tokenizer_type):
    """Load the appropriate tokenizer.

    Returns:
        (tokenizer, tokenizer_type_str)
    """
    if tokenizer_type == "tiktoken":
        import tiktoken
        print("Using tiktoken (cl100k_base) tokenizer")
        return tiktoken.get_encoding("cl100k_base"), "tiktoken"
    elif tokenizer_type == "qwen":
        from transformers import AutoTokenizer
        print("Loading Qwen2.5-VL tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen2-VL-7B-Instruct",
            trust_remote_code=True,
        )
        print("Qwen2.5-VL tokenizer loaded successfully")
        return tokenizer, "qwen"
    else:
        raise ValueError(f"Unknown tokenizer type: {tokenizer_type}")


def count_tokens(text, tokenizer, tokenizer_type):
    """Count the number of tokens in *text*."""
    if tokenizer_type == "tiktoken":
        return len(tokenizer.encode(text))
    elif tokenizer_type == "qwen":
        return len(tokenizer.encode(text, add_special_tokens=False))
    else:
        raise ValueError(f"Unknown tokenizer type: {tokenizer_type}")


def get_assistant_response(entry):
    """Extract the assistant/GPT response from an entry.

    Supports both conversation schemas:
      1. "conversations": [{"from": "gpt", "value": "..."}]
      2. "messages": [{"role": "assistant", "content": "..."}]

    Returns the response text or None if not found.
    """
    # Try "conversations" format first
    for conv in entry.get("conversations", []):
        if conv.get("from") == "gpt":
            return conv.get("value")

    # Try "messages" format
    for msg in entry.get("messages", []):
        if msg.get("role") == "assistant":
            return msg.get("content")

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Filter JSONL entries where assistant response exceeds token limit",
    )
    parser.add_argument("--jsonl", required=True, help="Path to input JSONL file")
    parser.add_argument("--output", default=None,
                        help="Output JSONL path (default: <input>_filtered.jsonl)")
    parser.add_argument("--tokenizer", choices=["tiktoken", "qwen"], default="tiktoken",
                        help="Tokenizer to use (default: tiktoken)")
    parser.add_argument("--max-tokens", type=int, default=8000,
                        help="Maximum token count threshold (default: 8000)")
    args = parser.parse_args()

    if args.output is None:
        p = Path(args.jsonl)
        args.output = str(p.with_stem(p.stem + "_filtered"))

    if not Path(args.jsonl).exists():
        print(f"Error: Input file not found: {args.jsonl}")
        return 1

    tokenizer, tokenizer_type = load_tokenizer(args.tokenizer)
    print()

    with open(args.jsonl) as f:
        lines = f.readlines()

    total = len(lines)
    print(f"Total entries: {total}")
    print(f"Max tokens: {args.max_tokens}")
    print("Scanning...\n")

    valid_lines = []
    filtered_count = 0
    bad_json_count = 0
    no_response_count = 0

    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            bad_json_count += 1
            print(f"  [BAD] line {i}: invalid JSON")
            continue

        response = get_assistant_response(entry)
        if response is None:
            # Keep entries with no assistant response (may be metadata, etc.)
            no_response_count += 1
            valid_lines.append(line)
            continue

        token_count = count_tokens(response, tokenizer, tokenizer_type)
        if token_count <= args.max_tokens:
            valid_lines.append(line)
        else:
            filtered_count += 1
            entry_id = entry.get("id", i)
            print(f"  [FILTERED] line {i} (id={entry_id}): {token_count} tokens")

        if (i + 1) % 5000 == 0:
            print(f"  Checked {i + 1}/{total}... "
                  f"(filtered: {filtered_count}, bad json: {bad_json_count})")

    with open(args.output, "w") as f:
        f.writelines(valid_lines)

    kept = len(valid_lines)
    print()
    print("=" * 60)
    print("FILTERING STATISTICS")
    print("=" * 60)
    print(f"Total entries:          {total}")
    if total > 0:
        print(f"Filtered (tokens):      {filtered_count} ({filtered_count / total * 100:.2f}%)")
        print(f"Invalid JSON:           {bad_json_count} ({bad_json_count / total * 100:.2f}%)")
        print(f"No assistant response:  {no_response_count}")
        print(f"Remaining:              {kept} ({kept / total * 100:.2f}%)")
    print("=" * 60)
    print(f"\nFiltered JSONL written to: {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
