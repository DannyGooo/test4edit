#!/usr/bin/env python3
"""
Transform ms_swift JSONL dataset to DPO format with length-matched loopy rejected samples.

This is an enhanced version of transform_to_dpo.py that guarantees:
  len(rejected_response) == len(response)

This eliminates length bias in DPO training — the model can only learn from
content quality (non-repetitive vs repetitive), not from output length.

Input format (JSONL, one JSON per line):
  {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Output format (DPO JSONL, one JSON per line):
  {
    "query": "<image>\\nUser prompt...",
    "response": "<!DOCTYPE html>...",
    "rejected_response": "<!DOCTYPE html>... [loopy content]",
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Usage:
  python3 transform_to_dpo_length_matched.py --input data.jsonl --output data_dpo.jsonl [--limit N] [--seed S]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List


# =============================================================================
# Loop generation utilities
# =============================================================================

def get_leading_indent(content: str, pos: int) -> str:
    """
    Extract the leading whitespace (indentation) of the line containing `pos`.
    Returns the whitespace prefix including the preceding newline so that
    repeated units stay on their own indented lines.
    """
    line_start = content.rfind('\n', 0, pos)
    if line_start == -1:
        indent = content[:pos]
        if indent and indent.strip() == '':
            return indent
        return ''
    after_newline = line_start + 1
    indent = ''
    for ch in content[after_newline:pos]:
        if ch in (' ', '\t'):
            indent += ch
        else:
            break
    if indent:
        return '\n' + indent
    return '\n'


def fill_to_length(unit: str, remaining: int) -> str:
    """
    Repeat `unit` and trim to exactly `remaining` characters.
    Guarantees the output is always exactly `remaining` chars long.
    """
    if not unit or remaining <= 0:
        return ''
    reps = (remaining // len(unit)) + 1
    return (unit * reps)[:remaining]


def is_in_css_region(content: str, pos: int) -> bool:
    """Check if position is within a <style> block."""
    style_open_pattern = re.compile(r'<style[^>]*>', re.IGNORECASE)
    style_close_pattern = re.compile(r'</style>', re.IGNORECASE)

    style_regions = []
    for match in style_open_pattern.finditer(content):
        start = match.end()
        close_match = style_close_pattern.search(content, start)
        if close_match:
            style_regions.append((start, close_match.start()))

    for start, end in style_regions:
        if start <= pos < end:
            return True
    return False


def extract_nearest_tag(content: str, pos: int) -> Optional[str]:
    """Extract the nearest HTML tag at or after the given position."""
    tag_pattern = re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?\s*/?>')

    match = tag_pattern.search(content, pos)
    if match:
        return match.group()

    for i in range(pos, -1, -1):
        match = tag_pattern.match(content, i)
        if match:
            return match.group()

    return None


def extract_html_section(content: str, pos: int) -> Optional[str]:
    """Extract a complete HTML section (element with content) near the position."""
    tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)[^>]*>')

    search_start = max(0, pos - 200)
    matches = list(tag_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    start = closest_match.start()

    if closest_match.group().endswith('/>'):
        return closest_match.group()

    close_pattern = re.compile(rf'</{tag_name}>', re.IGNORECASE)
    open_pattern = re.compile(rf'<{tag_name}[^>]*>', re.IGNORECASE)

    depth = 1
    search_pos = closest_match.end()

    while depth > 0 and search_pos < len(content):
        close_match = close_pattern.search(content, search_pos)
        open_match = open_pattern.search(content, search_pos)

        if not close_match:
            return content[start:min(start + 100, len(content))]

        if open_match and open_match.start() < close_match.start():
            depth += 1
            search_pos = open_match.end()
        else:
            depth -= 1
            if depth == 0:
                return content[start:close_match.end()]
            search_pos = close_match.end()

    return content[start:min(start + 100, len(content))]


def extract_css_rule(content: str, pos: int) -> Optional[str]:
    """Extract a complete CSS rule block near the position."""
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    search_start = max(0, pos - 500)
    matches = list(css_rule_pattern.finditer(content, search_start, min(pos + 500, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group().strip()


def extract_class_from_tag(content: str, pos: int) -> Tuple[Optional[str], Optional[str]]:
    """Extract the tag and class name from the nearest tag with a single class."""
    tag_with_class = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)\s+class="([^"]+)"[^>]*>')

    search_start = max(0, pos - 200)
    matches = list(tag_with_class.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None, None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    class_name = closest_match.group(2)
    full_tag = closest_match.group()

    return full_tag, class_name


def extract_css_property(content: str, pos: int) -> Optional[str]:
    """Extract a single CSS property near the position."""
    property_pattern = re.compile(r'[\w-]+\s*:\s*[^;{}]+;?')

    search_start = max(0, pos - 200)
    matches = list(property_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    prop = closest_match.group().strip()
    if not prop.endswith(';'):
        prop += ';'
    return prop


def extract_css_selector(content: str, pos: int) -> Optional[str]:
    """Extract a CSS selector near the position."""
    selector_pattern = re.compile(r'([.#]?[\w-]+(?:\s+[.#]?[\w-]+)*)\s*\{')

    search_start = max(0, pos - 300)
    matches = list(selector_pattern.finditer(content, search_start, min(pos + 300, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group(1).strip()


def extract_css_rules_group(content: str, pos: int, num_rules: int = 2) -> Optional[str]:
    """Extract multiple consecutive CSS rules as a group."""
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    all_matches = list(css_rule_pattern.finditer(content))

    if len(all_matches) < num_rules:
        return None

    closest_idx = 0
    min_dist = float('inf')
    for i, match in enumerate(all_matches):
        dist = abs(match.start() - pos)
        if dist < min_dist:
            min_dist = dist
            closest_idx = i

    start_idx = min(closest_idx, len(all_matches) - num_rules)
    start_idx = max(0, start_idx)

    rules = []
    for i in range(start_idx, min(start_idx + num_rules, len(all_matches))):
        rules.append(all_matches[i].group().strip())

    if len(rules) < 2:
        return None

    return '\n'.join(rules)


# =============================================================================
# HTML loop generators (length-matched)
# =============================================================================

def generate_char_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating 1-2 characters."""
    prefix = content[:start_pos]

    num_chars = random.choice([1, 2])
    if start_pos < len(content):
        loop_chars = content[start_pos:min(start_pos + num_chars, len(content))]
    else:
        loop_chars = 'a'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    suffix = fill_to_length(loop_chars, remaining)
    return prefix + suffix


def generate_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating an HTML tag."""
    prefix = content[:start_pos]

    tag = extract_nearest_tag(content, start_pos)
    if not tag:
        return generate_char_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_section_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating an HTML section."""
    prefix = content[:start_pos]

    section = extract_html_section(content, start_pos)
    if not section:
        return generate_tag_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + section
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_incrementing_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with incrementing class numbers."""
    prefix = content[:start_pos]

    tag_template, class_name = extract_class_from_tag(content, start_pos)
    if not tag_template or not class_name:
        return generate_tag_loop(content, start_pos, target_length)

    tag_match = re.match(r'<([a-zA-Z][a-zA-Z0-9]*)', tag_template)
    if not tag_match:
        return generate_tag_loop(content, start_pos, target_length)

    tag_name = tag_match.group(1)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    suffix = ""
    counter = 1
    while len(suffix) < remaining:
        new_tag = f'{indent}<{tag_name} class="{class_name}-{counter}">'
        suffix += new_tag
        counter += 1
    suffix = suffix[:remaining]

    return prefix + suffix


def generate_closing_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating closing tags."""
    prefix = content[:start_pos]

    closing_tag_pattern = re.compile(r'</[a-zA-Z][a-zA-Z0-9]*>')
    match = closing_tag_pattern.search(content, max(0, start_pos - 50))

    if match:
        closing_tag = match.group()
    else:
        closing_tag = '</div>'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + closing_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_self_closing_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating self-closing tags."""
    prefix = content[:start_pos]

    self_closing_pattern = re.compile(r'<(br|hr|img|input|meta|link)[^>]*/?>',re.IGNORECASE)
    match = self_closing_pattern.search(content, max(0, start_pos - 100))

    if match:
        self_closing_tag = match.group()
    else:
        self_closing_tag = random.choice(['<br/>', '<hr/>', '<input/>', '<img src=""/>'])

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + self_closing_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_deeply_nested_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with deep nesting (opening tags only)."""
    prefix = content[:start_pos]

    opening_tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)(?:\s+[^>]*)?>(?!/)')
    match = opening_tag_pattern.search(content, max(0, start_pos - 50))

    if match:
        tag_name = match.group(1)
        opening_tag = f'<{tag_name}>'
    else:
        opening_tag = '<div>'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + opening_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


# =============================================================================
# CSS loop generators (length-matched)
# =============================================================================

def generate_css_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating a CSS rule."""
    prefix = content[:start_pos]

    css_rule = extract_css_rule(content, start_pos)
    if not css_rule:
        return generate_char_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + css_rule + '\n'
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_multi_rule_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating multiple CSS rules together."""
    prefix = content[:start_pos]

    num_rules = random.choice([2, 3])
    rules_group = extract_css_rules_group(content, start_pos, num_rules)

    if not rules_group:
        return generate_css_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    unit = indent + rules_group + '\n'
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_property_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating a CSS property."""
    prefix = content[:start_pos]

    css_property = extract_css_property(content, start_pos)
    if not css_property:
        css_property = 'color: red;'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    unit = css_property + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_selector_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating CSS selectors."""
    prefix = content[:start_pos]

    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.class'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    unit = selector + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_incrementing_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with incrementing CSS selectors."""
    prefix = content[:start_pos]

    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.item'

    base_selector = re.sub(r'-?\d+$', '', selector)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    indent = get_leading_indent(content, start_pos)
    suffix = ""
    counter = 1
    while len(suffix) < remaining:
        rule = f'{indent}{base_selector}-{counter} {{ }}\n'
        suffix += rule
        counter += 1
    suffix = suffix[:remaining]

    return prefix + suffix


def generate_css_value_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating CSS values."""
    prefix = content[:start_pos]

    value_pattern = re.compile(r':\s*([\d.]+(?:px|em|rem|%|vh|vw)?|\w+)')
    match = value_pattern.search(content, max(0, start_pos - 100))

    if match:
        value = match.group(1)
    else:
        value = '10px'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    unit = value + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


# =============================================================================
# Core loop generation dispatcher
# =============================================================================

def generate_loopy_content(content: str) -> Tuple[str, str]:
    """
    Generate loopy rejected content from the given ground truth.
    Guarantees len(result) == len(content).

    Returns:
        Tuple of (loopy_content, loop_type_used)
    """
    start_pct = random.uniform(0.01, 0.95)
    start_pos = int(len(content) * start_pct)
    target_length = len(content)

    if is_in_css_region(content, start_pos):
        css_choices = ["css_rule", "css_property", "css_selector", "css_incrementing", "css_value", "css_multi_rule"]
        loop_type = random.choice(css_choices)

        if loop_type == "css_rule":
            result = generate_css_loop(content, start_pos, target_length)
        elif loop_type == "css_property":
            result = generate_css_property_loop(content, start_pos, target_length)
        elif loop_type == "css_selector":
            result = generate_css_selector_loop(content, start_pos, target_length)
        elif loop_type == "css_incrementing":
            result = generate_css_incrementing_loop(content, start_pos, target_length)
        elif loop_type == "css_value":
            result = generate_css_value_loop(content, start_pos, target_length)
        else:
            result = generate_css_multi_rule_loop(content, start_pos, target_length)
    else:
        html_choices = ["char", "tag", "section", "incrementing", "closing_tag", "self_closing", "deeply_nested"]
        loop_type = random.choice(html_choices)

        if loop_type == "char":
            result = generate_char_loop(content, start_pos, target_length)
        elif loop_type == "tag":
            result = generate_tag_loop(content, start_pos, target_length)
        elif loop_type == "section":
            result = generate_section_loop(content, start_pos, target_length)
        elif loop_type == "incrementing":
            result = generate_incrementing_tag_loop(content, start_pos, target_length)
        elif loop_type == "closing_tag":
            result = generate_closing_tag_loop(content, start_pos, target_length)
        elif loop_type == "self_closing":
            result = generate_self_closing_loop(content, start_pos, target_length)
        else:
            result = generate_deeply_nested_loop(content, start_pos, target_length)

    return result, loop_type


# =============================================================================
# ms_swift entry processing
# =============================================================================

def process_entry(entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Process a single ms_swift entry to DPO format.

    Input:  {"messages": [{"role": "user", "content": ...}, {"role": "assistant", "content": ...}], "images": [...]}
    Output: {"query": ..., "response": ..., "rejected_response": ..., "images": [...]}

    Returns:
        Tuple of (dpo_entry, skip_reason, loop_type)
    """
    user_msg = None
    assistant_msg = None

    for msg in entry.get("messages", []):
        role = msg.get("role", "")
        if role == "user":
            user_msg = msg.get("content", "")
        elif role == "assistant":
            assistant_msg = msg.get("content", "")

    if not user_msg:
        return None, "no_user_message", None

    if not assistant_msg:
        return None, "no_assistant_response", None

    try:
        rejected_content, loop_type = generate_loopy_content(assistant_msg)
    except Exception as e:
        return None, f"error: {str(e)}", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected_content,
        "images": entry.get("images", []),
    }

    return dpo_entry, None, loop_type


def main():
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL dataset to DPO format with length-matched loopy rejected samples"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input JSONL file path (ms_swift format)"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output JSONL file path (DPO format)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process (default: all)"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)"
    )

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    # Track statistics
    total = 0
    written = 0
    skipped = 0
    length_mismatches = 0
    skip_reasons: Dict[str, List[str]] = {}
    loop_type_counts = {
        "char": 0, "tag": 0, "section": 0, "incrementing": 0,
        "closing_tag": 0, "self_closing": 0, "deeply_nested": 0,
        "css_rule": 0, "css_property": 0, "css_selector": 0,
        "css_incrementing": 0, "css_value": 0, "css_multi_rule": 0,
    }

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print("\nProcessing entries...")

    with open(args.input, "r", encoding="utf-8") as fin, \
         open(args.output, "w", encoding="utf-8") as fout:

        for i, line in enumerate(fin):
            if args.limit and written >= args.limit:
                break

            line = line.strip()
            if not line:
                continue

            total += 1

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping line {i + 1} (invalid JSON): {e}", file=sys.stderr)
                skipped += 1
                if "invalid_json" not in skip_reasons:
                    skip_reasons["invalid_json"] = []
                skip_reasons["invalid_json"].append(f"line_{i + 1}")
                continue

            dpo_entry, skip_reason, loop_type = process_entry(entry)

            if skip_reason:
                skipped += 1
                if skip_reason not in skip_reasons:
                    skip_reasons[skip_reason] = []
                skip_reasons[skip_reason].append(f"line_{i + 1}")
            else:
                # Verify length matching
                if len(dpo_entry["rejected_response"]) != len(dpo_entry["response"]):
                    length_mismatches += 1

                fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                written += 1
                if loop_type:
                    loop_type_counts[loop_type] += 1

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries...")

    # Save skip report if there are skipped entries
    if skip_reasons:
        skip_file = args.output + ".skipped.json"
        print(f"\nSaving skip report to: {skip_file}")
        with open(skip_file, "w", encoding="utf-8") as f:
            json.dump(skip_reasons, f, indent=2, ensure_ascii=False)

    # Print statistics
    print("\n" + "=" * 50)
    print("TRANSFORMATION STATISTICS (LENGTH-MATCHED)")
    print("=" * 50)
    print(f"Total lines processed: {total}")
    print(f"Successfully transformed: {written}")
    print(f"Skipped: {skipped}")
    print(f"Length mismatches: {length_mismatches}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, entries in skip_reasons.items():
            print(f"  {reason}: {len(entries)}")

    print("\nLoop type distribution:")
    for loop_type, count in loop_type_counts.items():
        if count > 0:
            pct = (count / written * 100) if written else 0
            print(f"  {loop_type}: {count} ({pct:.1f}%)")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
