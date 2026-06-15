#!/usr/bin/env python3
"""
Combined 50/50 DPO dataset: half entries get infinite-nesting repetition
rejected samples (equal-share 25-pattern uniform distribution, NO length
match), half get semantic full-coverage perturbation.

Sibling to transform_to_dpo_combined.py which routes to the frequency-
weighted no_length_match variant. This variant instead routes to the
equal-share 25-pattern taxonomy so each repetition sub-pattern contributes
~1/25 of the repetition half, while dropping the length-match guarantee
so rejected length can vary (target_length drawn as a random fraction of
len(chosen), same shaping as transform_to_dpo_infinite_nesting_no_length_match.py).

  - Repetition half: reuses helpers from
    transform_to_dpo_infinite_nesting_equal_share.py (ALL_EQUAL_SHARE_PATTERNS,
    _generate_cf_html_fallback, _dispatch_cf_prereq_pattern, and the inline
    loop primitives) but with randomized target_length for inline +
    cf_html_fallback patterns.  Prerequisite-dependent cf_* generators
    (whitespace_runaway / css_bloat / section_repetition / …) keep their
    internal target=len(content) — same as in the frequency-weighted
    no_length_match script — because they are not length-parameterized.
  - Semantic half: calls process_entry from
    transform_to_dpo_semantic_full_coverage.py (16 perturbation axes).

If the semantic path fails for an entry (e.g. HTML has no CSS to perturb),
the entry falls back to repetition so no entries are wasted.

Output format is identical to both source scripts:
  {"query": "...", "response": "...", "rejected_response": "...", "images": [...]}

An extra field "rejection_mode" ("repetition" or "semantic") is added to
each output record so downstream analysis can split by mode. Another
field "rejection_sub_pattern" is added for repetition entries so the
equal-share distribution is auditable (inline_* / cf_* taxonomy).

Usage:
  python3 transform_to_dpo_combined_equal_share.py --input data.jsonl --output data_dpo.jsonl \
      [--limit N] [--seed S] [--perturb-rate 0.7] [--categories all] [--split 0.5]
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import transform_to_dpo_infinite_nesting_equal_share as eq_mod
import transform_to_dpo_semantic_full_coverage as sem_mod


# -----------------------------------------------------------------------------
# Repetition generator: equal-share dispatch + no-length-match target sampling.
# -----------------------------------------------------------------------------
#
# We reuse equal_share's taxonomy (ALL_EQUAL_SHARE_PATTERNS) and its helper
# functions for the cf_html_fallback and inline loops, but override the
# target_length so it is drawn as a random fraction of len(content) instead
# of being pinned to len(content). This mirrors the target-sampling policy
# of transform_to_dpo_infinite_nesting_no_length_match.py's
# generate_inline_repetition().
#
# Note: the prerequisite-dependent cf_* generators (whitespace_runaway /
# css_bloat / section_repetition / self_closing_spam / closing_tag_spam /
# truncated_padded / css_rule_cycling / enumeration) compute target =
# len(content) internally. They are not length-parameterized in either the
# equal_share or no_length_match script, so we call them unchanged. The
# overall pipeline is therefore "no length match" in the same sense as the
# sibling no_length_match.py — variable length for inline + cf_html_fallback,
# approximately len(content) for structured cf_* generators.

_INLINE_LOOP_DISPATCH = {
    'char': eq_mod.generate_char_loop,
    'tag': eq_mod.generate_tag_loop,
    'section': eq_mod.generate_section_loop,
    'incrementing': eq_mod.generate_incrementing_tag_loop_inline,
    'closing_tag': eq_mod.generate_closing_tag_loop,
    'self_closing': eq_mod.generate_self_closing_loop,
    'deeply_nested': eq_mod.generate_deeply_nested_loop,
    'css_rule': eq_mod.generate_css_loop,
    'css_property': eq_mod.generate_css_property_loop,
    'css_selector': eq_mod.generate_css_selector_loop,
    'css_incrementing': eq_mod.generate_css_incrementing_loop,
    'css_value': eq_mod.generate_css_value_loop,
    'css_multi_rule': eq_mod.generate_css_multi_rule_loop,
}


def _inline_forced_variable_length(
    content: str, loop_type: str, target_length: int
) -> Optional[Tuple[str, str]]:
    """Force a specific inline loop type with a caller-supplied target_length.

    Analogous to eq_mod.generate_inline_repetition_forced but the caller
    controls target_length (so it can be < len(content) to yield a shorter
    rejected sample). Start position is anchored inside target_length so the
    loop generator has room to write its suffix — mirrors no_length_match.py.
    """
    if loop_type in eq_mod.INLINE_CSS_TYPES:
        start_pos = eq_mod._pick_css_start_pos(content)
        if start_pos is None:
            return None
    else:
        start_pos = eq_mod._pick_html_start_pos(content)

    # Anchor start_pos inside target_length so the generator has room to grow.
    # If the naturally-picked start_pos would overshoot target_length, redraw
    # it as a fraction of target_length (same trick as no_length_match.py's
    # generate_inline_repetition).
    if start_pos >= target_length:
        start_pos = int(target_length * random.uniform(0.01, 0.95))

    fn = _INLINE_LOOP_DISPATCH.get(loop_type)
    if fn is None:
        return None
    result = fn(content, start_pos, target_length)
    return result, f'inline_{loop_type}'


def generate_equal_share_no_length_match(content: str) -> Tuple[str, str]:
    """Equal-share (25-pattern uniform) dispatch with randomized target_length.

    Each call draws target_length ∈ [max(64, base_len // 3), base_len] and
    picks uniformly from ALL_EQUAL_SHARE_PATTERNS. Prerequisite-dependent
    cf_* patterns re-roll if they cannot produce output from this content.

    Returns (rejected_content, sub_pattern_label) — sub_pattern is one of
    'cf_*' or 'inline_*' exactly as in the source scripts.
    """
    base_len = len(content)
    if base_len == 0:
        return content, 'cf_pure'

    # Randomize target length so rejected samples have variable length —
    # same shaping as transform_to_dpo_infinite_nesting_no_length_match.py.
    target_length = random.randint(max(64, base_len // 3), base_len)

    tried: set = set()
    for _ in range(len(eq_mod.ALL_EQUAL_SHARE_PATTERNS) * 2):
        candidates_list = [p for p in eq_mod.ALL_EQUAL_SHARE_PATTERNS if p not in tried]
        if not candidates_list:
            break
        mode, pattern = random.choice(candidates_list)
        tried.add((mode, pattern))

        if mode == 'inline':
            result = _inline_forced_variable_length(content, pattern, target_length)
            if result is not None:
                return result
            # inline_css_* patterns need a <style> block; fall through on miss.

        else:  # cf
            if pattern in ('cycling', 'incrementing', 'pure'):
                # HTML fallback generators accept target_length directly.
                return eq_mod._generate_cf_html_fallback(content, pattern, target_length)

            result = eq_mod._dispatch_cf_prereq_pattern(content, pattern)
            if result is not None:
                rej, name = result
                return rej, f'cf_{name}'
            # prerequisite missing — re-roll.

    # Last resort: always-applicable HTML fallback with randomized target.
    fallback = random.choice(['cycling', 'incrementing', 'pure'])
    return eq_mod._generate_cf_html_fallback(content, fallback, target_length)


def _process_entry_repetition(entry: Dict[str, Any]):
    """Local process_entry for the repetition half — same signature as
    the sibling modules' process_entry."""
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
        rejected_content, sub_pattern = generate_equal_share_no_length_match(assistant_msg)
    except Exception as e:
        return None, f"error: {str(e)}", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected_content,
        "images": entry.get("images", []),
    }
    return dpo_entry, None, sub_pattern


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Combined 50/50 DPO: half infinite-nesting repetition (equal-share "
            "25-pattern uniform, NO length match), half semantic full-coverage "
            "perturbation"
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
    length_mismatches = 0
    skip_reasons: Dict[str, int] = {}

    # Repetition sub-pattern distribution (for equal-share auditing)
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
    print(f"Repetition mode: equal-share 25-pattern uniform, NO length match")
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
            sub_pattern = None

            if use_semantic:
                dpo_entry, skip_reason, stats = sem_mod.process_entry(
                    entry, args.perturb_rate, flags
                )
                if skip_reason:
                    # Semantic failed (no CSS etc.) — fall back to repetition
                    # so we don't waste the entry.
                    sem_fallback_to_rep += 1
                    dpo_entry, skip_reason, sub_pattern = _process_entry_repetition(entry)
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
                dpo_entry, skip_reason, sub_pattern = _process_entry_repetition(entry)
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

            # Track length mismatches (informational only; no length-match
            # guarantee in this variant).
            if len(dpo_entry["rejected_response"]) != len(dpo_entry["response"]):
                length_mismatches += 1

            # Annotate with rejection mode and sub-pattern for downstream analysis.
            dpo_entry["rejection_mode"] = mode
            if mode == "repetition" and sub_pattern:
                dpo_entry["rejection_sub_pattern"] = sub_pattern

            fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
            written += 1

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries "
                      f"(rep={rep_written}, sem={sem_written})...")

    # --- Stats ---
    print("\n" + "=" * 70)
    print("COMBINED TRANSFORMATION STATISTICS "
          "(EQUAL-SHARE REPETITION + SEMANTIC FULL-COVERAGE, NO LENGTH MATCH)")
    print("=" * 70)
    print(f"Total lines processed:  {total}")
    print(f"Successfully written:   {written}")
    print(f"  - Repetition:         {rep_written}")
    print(f"  - Semantic:           {sem_written}")
    print(f"Skipped:                {skipped}")
    print(f"Semantic→repetition fallback: {sem_fallback_to_rep}")
    print(f"Length mismatches:      {length_mismatches} "
          f"(expected — no length-match guarantee)")

    if written > 0:
        rep_pct = rep_written / written * 100
        sem_pct = sem_written / written * 100
        print(f"\nActual split: {sem_pct:.1f}% semantic / {rep_pct:.1f}% repetition")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")

    # Repetition sub-pattern distribution — the equal-share design targets
    # ~1/25 per sub-pattern on the repetition half.
    if rep_sub_pattern_counts:
        cf_total = sum(c for k, c in rep_sub_pattern_counts.items() if k.startswith('cf_'))
        inline_total = sum(c for k, c in rep_sub_pattern_counts.items() if k.startswith('inline_'))
        print(f"\nRepetition mode distribution:")
        if rep_written:
            print(f"  Completion failure (cf_*): {cf_total} "
                  f"({cf_total/rep_written*100:.1f}% of rep)")
            print(f"  Inline repetition (inline_*): {inline_total} "
                  f"({inline_total/rep_written*100:.1f}% of rep)")
        print(f"\n  Sub-pattern breakdown (target: ~{100/25:.1f}% each):")
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
