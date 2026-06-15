#!/usr/bin/env python3
"""
Combined 50/50 DPO dataset: half entries get infinite-nesting repetition
rejected samples, half get semantic full-coverage perturbation.

This script imports both generator modules and dispatches per-entry:

  - Repetition half: calls process_entry from
    transform_to_dpo_infinite_nesting_no_length_match.py
    (25 sub-patterns, no length-match guarantee)
  - Semantic half: calls process_entry from
    transform_to_dpo_semantic_full_coverage.py
    (16 perturbation axes, CSS + HTML attributes)

If the semantic path fails for an entry (e.g. HTML has no CSS to perturb),
the entry falls back to repetition so no entries are wasted.

Output format is identical to both source scripts:
  {"query": "...", "response": "...", "rejected_response": "...", "images": [...]}

An extra field "rejection_mode" ("repetition" or "semantic") is added to
each output record so downstream analysis can split by mode.

Usage:
  python3 transform_to_dpo_combined.py --input data.jsonl --output data_dpo.jsonl \
      [--limit N] [--seed S] [--perturb-rate 0.7] [--categories all] [--split 0.5]
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import transform_to_dpo_infinite_nesting_no_length_match as rep_mod
import transform_to_dpo_semantic_full_coverage as sem_mod


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Combined 50/50 DPO: half infinite-nesting repetition, "
            "half semantic full-coverage perturbation"
        )
    )
    parser.add_argument("--input", "-i", required=True,
                        help="Input JSONL file path (ms_swift format)")
    parser.add_argument("--output", "-o", required=True,
                        help="Output JSONL file path (DPO format)")
    parser.add_argument("--limit", "-l", type=int, default=None,
                        help="Limit number of entries (default: all)")
    parser.add_argument("--seed", "-s", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument(
        "--perturb-rate", type=float, default=0.7,
        help="Per-value perturbation probability for semantic half (default: 0.7)",
    )
    parser.add_argument(
        "--categories", "-c", type=str, default="all",
        help=(
            "Comma-separated semantic categories to enable. "
            "Valid: " + ",".join(sem_mod.ALL_CATEGORIES) + ". "
            "Default: 'all'."
        ),
    )
    parser.add_argument(
        "--split", type=float, default=0.5,
        help=(
            "Fraction of entries routed to semantic perturbation [0.0, 1.0]. "
            "The rest go to repetition. Default: 0.5 (50/50)."
        ),
    )

    args = parser.parse_args()

    if not (0.0 < args.perturb_rate <= 1.0):
        print(f"Error: --perturb-rate must be in (0.0, 1.0], got {args.perturb_rate}",
              file=sys.stderr)
        sys.exit(1)

    if not (0.0 <= args.split <= 1.0):
        print(f"Error: --split must be in [0.0, 1.0], got {args.split}",
              file=sys.stderr)
        sys.exit(1)

    try:
        flags = sem_mod.CategoryFlags.from_csv(args.categories)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    # --- Counters ---
    total = 0
    written = 0
    skipped = 0
    rep_written = 0
    sem_written = 0
    sem_fallback_to_rep = 0
    skip_reasons: Dict[str, int] = {}

    # Repetition sub-pattern distribution
    rep_sub_pattern_counts: Dict[str, int] = {}

    # Semantic per-category totals
    sem_stat_name_to_slot = {
        'colors': 'colors', 'dimensions': 'dimensions',
        'opacity': 'opacity', 'fonts': 'fonts',
        'font_weight': 'font_weights', 'transform': 'transforms',
        'display': 'display', 'img_dims': 'img_dims',
        'text_style': 'text_style', 'position': 'position',
        'overflow': 'overflow', 'border_style': 'border_style',
        'filter': 'filter', 'background': 'background',
        'unitless_number': 'unitless_number', 'table_attrs': 'table_attrs',
    }
    sem_totals: Dict[str, int] = {name: 0 for name in sem_mod.ALL_CATEGORIES}

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print(f"Split: {args.split:.0%} semantic / {1 - args.split:.0%} repetition")
    print(f"Semantic perturb rate: {args.perturb_rate}")
    enabled_names = [c for c in sem_mod.ALL_CATEGORIES if getattr(flags, c)]
    print(f"Semantic categories ({len(enabled_names)}/{len(sem_mod.ALL_CATEGORIES)}): "
          f"{','.join(enabled_names)}")
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
                print(f"Warning: Skipping line {i + 1} (invalid JSON): {e}",
                      file=sys.stderr)
                skipped += 1
                skip_reasons["invalid_json"] = skip_reasons.get("invalid_json", 0) + 1
                continue

            # ----- Dispatch: semantic vs repetition -----
            use_semantic = random.random() < args.split
            mode = None
            dpo_entry = None

            if use_semantic:
                dpo_entry, skip_reason, stats = sem_mod.process_entry(
                    entry, args.perturb_rate, flags
                )
                if skip_reason:
                    # Semantic failed (no CSS etc.) — fall back to repetition
                    # so we don't waste the entry.
                    sem_fallback_to_rep += 1
                    dpo_entry, skip_reason, sub_pattern = rep_mod.process_entry(entry)
                    if skip_reason:
                        skipped += 1
                        skip_reasons[f"both_failed:{skip_reason}"] = (
                            skip_reasons.get(f"both_failed:{skip_reason}", 0) + 1
                        )
                        continue
                    mode = "repetition"
                    rep_written += 1
                    if sub_pattern:
                        rep_sub_pattern_counts[sub_pattern] = (
                            rep_sub_pattern_counts.get(sub_pattern, 0) + 1
                        )
                else:
                    mode = "semantic"
                    sem_written += 1
                    for cat_name, slot in sem_stat_name_to_slot.items():
                        sem_totals[cat_name] += getattr(stats, slot)
            else:
                dpo_entry, skip_reason, sub_pattern = rep_mod.process_entry(entry)
                if skip_reason:
                    skipped += 1
                    skip_reasons[f"rep:{skip_reason}"] = (
                        skip_reasons.get(f"rep:{skip_reason}", 0) + 1
                    )
                    continue
                mode = "repetition"
                rep_written += 1
                if sub_pattern:
                    rep_sub_pattern_counts[sub_pattern] = (
                        rep_sub_pattern_counts.get(sub_pattern, 0) + 1
                    )

            # Annotate with rejection mode for downstream analysis
            dpo_entry["rejection_mode"] = mode
            fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
            written += 1

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries "
                      f"(rep={rep_written}, sem={sem_written})...")

    # --- Stats ---
    print("\n" + "=" * 65)
    print("COMBINED TRANSFORMATION STATISTICS (REPETITION + SEMANTIC)")
    print("=" * 65)
    print(f"Total lines processed:  {total}")
    print(f"Successfully written:   {written}")
    print(f"  - Repetition:         {rep_written}")
    print(f"  - Semantic:           {sem_written}")
    print(f"Skipped:                {skipped}")
    print(f"Semantic→repetition fallback: {sem_fallback_to_rep}")

    if written > 0:
        rep_pct = rep_written / written * 100
        sem_pct = sem_written / written * 100
        print(f"\nActual split: {sem_pct:.1f}% semantic / {rep_pct:.1f}% repetition")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")

    # Repetition sub-pattern distribution
    if rep_sub_pattern_counts:
        cf_total = sum(c for k, c in rep_sub_pattern_counts.items() if k.startswith('cf_'))
        inline_total = sum(c for k, c in rep_sub_pattern_counts.items() if k.startswith('inline_'))
        print(f"\nRepetition mode distribution:")
        if rep_written:
            print(f"  Completion failure (cf_*): {cf_total} "
                  f"({cf_total/rep_written*100:.1f}% of rep)")
            print(f"  Inline repetition (inline_*): {inline_total} "
                  f"({inline_total/rep_written*100:.1f}% of rep)")
        print(f"\n  Sub-pattern breakdown:")
        for sub, count in sorted(rep_sub_pattern_counts.items()):
            pct = count / rep_written * 100 if rep_written else 0
            print(f"    {sub}: {count} ({pct:.1f}%)")

    # Semantic per-category totals
    if sem_written > 0:
        print(f"\nSemantic perturbation totals ({sem_written} entries):")
        header = f"  {'category':<18} {'total':>8} {'avg/entry':>12}"
        print(header)
        print(f"  {'-' * 16:<18} {'-' * 6:>8} {'-' * 10:>12}")
        for cat in sem_mod.ALL_CATEGORIES:
            if not getattr(flags, cat):
                continue
            t = sem_totals[cat]
            avg = t / sem_written
            print(f"  {cat:<18} {t:>8} {avg:>12.2f}")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
