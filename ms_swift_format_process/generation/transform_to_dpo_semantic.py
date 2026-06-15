#!/usr/bin/env python3
"""
Transform ms_swift JSONL dataset to DPO format with SEMANTIC rejected samples.

Unlike the repetition-based sibling scripts (transform_to_dpo.py,
transform_to_dpo_infinite_nesting*.py), this variant keeps the full HTML
structure of the chosen response and perturbs visual semantics instead:

  1. COLOR perturbation  — hex, rgb()/rgba(), and named CSS colors inside
                            <style> blocks and style="..." attributes are
                            randomly remapped to other colors.
  2. DIMENSION perturbation — numeric values with px/em/rem/%/vh/vw/pt units
                              inside the same scopes are scaled by a random
                              multiplier drawn from a discrete set (0.25x,
                              0.5x, 2x, 3x, 4x).

The rejected HTML is therefore:
  - Syntactically valid and structurally identical to the chosen HTML
  - Visually wrong: wrong colors, wrong sizes/spacings
  - A harder signal for DPO than pure repetition (the model has to learn
    which values are "right" given the screenshot, not just avoid loops)

Perturbation is scoped to CSS text (inside <style>...</style> and style="...")
so text content, class names, image URLs, etc. are never touched.

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
    "rejected_response": "<!DOCTYPE html>... [semantic perturbation]",
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Usage:
  python3 transform_to_dpo_semantic.py --input data.jsonl --output data_dpo.jsonl [--limit N] [--seed S] [--perturb-rate 0.7]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Tuple, Optional, Dict, Any


# =============================================================================
# Color / dimension vocabularies and regexes
# =============================================================================

# CSS named colors we are willing to substitute. Limited to visually distinct
# colors so every swap is a meaningful perceptual change.
NAMED_COLORS = [
    'black', 'white', 'red', 'green', 'blue', 'yellow', 'orange', 'purple',
    'pink', 'brown', 'gray', 'grey', 'cyan', 'magenta', 'lime', 'navy',
    'teal', 'olive', 'maroon', 'silver', 'gold', 'indigo', 'violet', 'tan',
    'beige', 'coral', 'salmon', 'khaki', 'crimson', 'aqua', 'turquoise',
]
NAMED_COLOR_SET = {c.lower() for c in NAMED_COLORS}

# Hex colors (#rgb, #rgba, #rrggbb, #rrggbbaa)
HEX_COLOR_RE = re.compile(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{1})?(?:[0-9a-fA-F]{2})?(?:[0-9a-fA-F]{2})?\b')

# rgb() / rgba() functional notation
RGB_COLOR_RE = re.compile(r'rgba?\(\s*[\d.,\s%/]+\)', re.IGNORECASE)

# Named colors — matched with word boundaries so they don't eat class names
NAMED_COLOR_RE = re.compile(
    r'\b(' + '|'.join(sorted(NAMED_COLORS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

# Dimension values — numbers with a CSS length unit. Lookbehind prevents
# matching inside words/identifiers (e.g. class names with digits).
DIMENSION_RE = re.compile(
    r'(?<![\w.#-])(-?\d+(?:\.\d+)?)(px|em|rem|vh|vw|pt|cm|mm|in|%)\b',
    re.IGNORECASE,
)

# Discrete scale multipliers for dimension perturbation — every multiplier is
# far enough from 1.0 to be visually obvious.
DIM_MULTIPLIERS = [0.25, 0.5, 2.0, 3.0, 4.0]


# =============================================================================
# Perturbation primitives
# =============================================================================

def random_hex_color() -> str:
    """Return a random 6-digit hex color like '#a3f92c'."""
    return '#{:06x}'.format(random.randint(0, 0xFFFFFF))


def random_rgb_color() -> str:
    """Return a random rgb() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    return f'rgb({r}, {g}, {b})'


def random_rgba_color() -> str:
    """Return a random rgba() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    a = round(random.uniform(0.1, 1.0), 2)
    return f'rgba({r}, {g}, {b}, {a})'


def pick_different_named_color(current: str) -> str:
    """Pick a random named color that differs from `current`."""
    current_l = current.lower()
    choices = [c for c in NAMED_COLORS if c.lower() != current_l]
    return random.choice(choices)


def scale_dimension(value: float, unit: str) -> str:
    """Scale a dimension by a random multiplier from DIM_MULTIPLIERS."""
    factor = random.choice(DIM_MULTIPLIERS)
    new_val = value * factor
    # Preserve integer formatting when possible
    if abs(new_val - round(new_val)) < 1e-9:
        return f'{int(round(new_val))}{unit}'
    return f'{new_val:g}{unit}'


# =============================================================================
# CSS-text perturbation
# =============================================================================

class PerturbStats:
    """Mutable counters threaded through the regex callbacks so we can tell
    which entries actually received a meaningful perturbation."""
    __slots__ = ('colors', 'dimensions')

    def __init__(self) -> None:
        self.colors = 0
        self.dimensions = 0


def perturb_css_text(css: str, perturb_rate: float, stats: PerturbStats) -> str:
    """Apply color + dimension perturbation to a CSS text blob."""

    def sub_hex(m: 're.Match[str]') -> str:
        if random.random() < perturb_rate:
            stats.colors += 1
            return random_hex_color()
        return m.group(0)

    def sub_rgb(m: 're.Match[str]') -> str:
        if random.random() < perturb_rate:
            stats.colors += 1
            # Preserve rgb vs rgba notation when we can
            return random_rgba_color() if 'rgba' in m.group(0).lower() else random_rgb_color()
        return m.group(0)

    def sub_named(m: 're.Match[str]') -> str:
        if random.random() < perturb_rate:
            stats.colors += 1
            return pick_different_named_color(m.group(0))
        return m.group(0)

    def sub_dim(m: 're.Match[str]') -> str:
        if random.random() < perturb_rate:
            stats.dimensions += 1
            value = float(m.group(1))
            unit = m.group(2)
            return scale_dimension(value, unit)
        return m.group(0)

    # Order matters: perturb hex first (it contains digits that could confuse
    # the dimension regex), then rgb/rgba, then named colors, then dimensions.
    css = HEX_COLOR_RE.sub(sub_hex, css)
    css = RGB_COLOR_RE.sub(sub_rgb, css)
    css = NAMED_COLOR_RE.sub(sub_named, css)
    css = DIMENSION_RE.sub(sub_dim, css)
    return css


# =============================================================================
# HTML scoping: only touch CSS regions (<style> blocks + style="..." attrs)
# =============================================================================

STYLE_BLOCK_RE = re.compile(r'(<style\b[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
STYLE_ATTR_SINGLE_RE = re.compile(r"style\s*=\s*'([^']*)'", re.IGNORECASE)


def perturb_html(html: str, perturb_rate: float) -> Tuple[str, PerturbStats]:
    """Perturb CSS colors and dimensions inside every <style> block and
    style="..." attribute. Text content, tag names, class names and image
    URLs are never modified."""
    stats = PerturbStats()

    def sub_style_block(m: 're.Match[str]') -> str:
        return m.group(1) + perturb_css_text(m.group(2), perturb_rate, stats) + m.group(3)

    def sub_style_attr_double(m: 're.Match[str]') -> str:
        return 'style="' + perturb_css_text(m.group(1), perturb_rate, stats) + '"'

    def sub_style_attr_single(m: 're.Match[str]') -> str:
        return "style='" + perturb_css_text(m.group(1), perturb_rate, stats) + "'"

    html = STYLE_BLOCK_RE.sub(sub_style_block, html)
    html = STYLE_ATTR_RE.sub(sub_style_attr_double, html)
    html = STYLE_ATTR_SINGLE_RE.sub(sub_style_attr_single, html)
    return html, stats


# =============================================================================
# ms_swift entry processing
# =============================================================================

def process_entry(
    entry: Dict[str, Any],
    perturb_rate: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Process a single ms_swift entry → DPO entry with semantic rejection."""
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

    rejected, stats = perturb_html(assistant_msg, perturb_rate)

    # Skip entries where nothing could be perturbed (HTML has no CSS at all).
    if stats.colors == 0 and stats.dimensions == 0:
        return None, "no_perturbable_css", None

    # Skip pathological no-op (identical output). Very rare but worth catching.
    if rejected == assistant_msg:
        return None, "perturbation_no_op", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected,
        "images": entry.get("images", []),
    }
    label = f"semantic_c{stats.colors}_d{stats.dimensions}"
    return dpo_entry, None, label


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL dataset to DPO format with semantic (color/dimension) rejected samples"
    )
    parser.add_argument("--input", "-i", required=True, help="Input JSONL file path (ms_swift format)")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL file path (DPO format)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of entries (default: all)")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--perturb-rate",
        type=float,
        default=0.7,
        help="Per-value probability of perturbation [0.0, 1.0]. Higher = more aggressive (default: 0.7)",
    )

    args = parser.parse_args()

    if not (0.0 < args.perturb_rate <= 1.0):
        print(f"Error: --perturb-rate must be in (0.0, 1.0], got {args.perturb_rate}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    total = 0
    written = 0
    skipped = 0
    total_colors = 0
    total_dims = 0
    skip_reasons: Dict[str, int] = {}

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print(f"Perturb rate: {args.perturb_rate}")
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
                skip_reasons["invalid_json"] = skip_reasons.get("invalid_json", 0) + 1
                continue

            dpo_entry, skip_reason, label = process_entry(entry, args.perturb_rate)

            if skip_reason:
                skipped += 1
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            else:
                fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                written += 1
                if label:
                    # label format: semantic_c{colors}_d{dims}
                    parts = label.split('_')
                    total_colors += int(parts[1][1:])
                    total_dims += int(parts[2][1:])

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries...")

    print("\n" + "=" * 60)
    print("TRANSFORMATION STATISTICS (SEMANTIC: COLOR + DIMENSION)")
    print("=" * 60)
    print(f"Total lines processed: {total}")
    print(f"Successfully transformed: {written}")
    print(f"Skipped: {skipped}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")

    if written > 0:
        print(f"\nPerturbation totals:")
        print(f"  Total colors changed:     {total_colors}")
        print(f"  Total dimensions changed: {total_dims}")
        print(f"  Avg colors / entry:       {total_colors / written:.2f}")
        print(f"  Avg dimensions / entry:   {total_dims / written:.2f}")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
