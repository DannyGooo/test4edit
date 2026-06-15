#!/usr/bin/env python3
"""
Keep top-N JSON entries ranked by HTML "value density":

    score = value_containing_tags / total_html_tags

We score the GPT response found in entry["conversations"] where conv["from"] == "gpt".

Heuristic for a "value-containing" tag:
- The tag contains non-whitespace *direct text* (i.e., text data encountered while the tag is the current open element),
  excluding text inside script/style/noscript/template.
- OR the tag has a non-empty "value-like" attribute (e.g., value/placeholder/alt/title/aria-label/content/href/src).

This is intended to bias toward samples with more meaningful user-facing content relative to markup noise.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SKIP_TEXT_TAGS = {"script", "style", "noscript", "template"}
VALUE_ATTRS = {"value", "placeholder", "alt", "title", "aria-label", "content", "href", "src"}


def get_gpt_response_from_entry(entry: dict) -> str | None:
    """Extract the GPT response value from a conversation entry."""
    conversations = entry.get("conversations", [])
    for conv in conversations:
        if conv.get("from") == "gpt":
            return conv.get("value")
    return None


def _is_meaningful_attr(tag: str, attr_name: str, attr_value: Optional[str]) -> bool:
    if attr_value is None:
        return False
    v = str(attr_value).strip()
    if not v:
        return False
    if attr_name not in VALUE_ATTRS:
        return False
    # avoid counting placeholder href/src that are essentially empty or non-informative
    if attr_name == "href" and (v == "#" or v.lower().startswith("javascript:")):
        return False
    if attr_name == "src" and v.lower().startswith("data:"):
        # data URIs often add noise and can dominate; treat as not "value" by default
        return False
    return True


@dataclass
class _TagInfo:
    name: str
    has_direct_text: bool = False
    has_value_attr: bool = False


class _ValueDensityHTMLParser(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True makes data() more readable; ok for our scoring
        super().__init__(convert_charrefs=True)
        self.total_tags: int = 0
        self._stack: List[int] = []
        self._tags: Dict[int, _TagInfo] = {}
        self._next_id: int = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._on_start_tag(tag, attrs, is_self_closing=False)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        # Self-closing tag
        self._on_start_tag(tag, attrs, is_self_closing=True)

    def _on_start_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], is_self_closing: bool
    ) -> None:
        self.total_tags += 1
        tag_id = self._next_id
        self._next_id += 1

        info = _TagInfo(name=tag.lower())
        for k, v in attrs:
            k_norm = (k or "").lower()
            if _is_meaningful_attr(info.name, k_norm, v):
                info.has_value_attr = True
                break

        self._tags[tag_id] = info

        if not is_self_closing:
            self._stack.append(tag_id)

    def handle_endtag(self, tag: str) -> None:
        # HTML can be malformed; be defensive.
        if not self._stack:
            return
        # Pop until we find a matching tag name or empty stack.
        tag_norm = tag.lower()
        while self._stack:
            top_id = self._stack.pop()
            if self._tags.get(top_id, _TagInfo(tag_norm)).name == tag_norm:
                break

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        text = (data or "").strip()
        if not text:
            return

        # Mark the nearest open tag that isn't a skipped text container
        for tag_id in reversed(self._stack):
            info = self._tags.get(tag_id)
            if info is None:
                continue
            if info.name in SKIP_TEXT_TAGS:
                return  # ignore all text within these tags
            info.has_direct_text = True
            return

    @property
    def value_tags(self) -> int:
        return sum(1 for t in self._tags.values() if t.has_direct_text or t.has_value_attr)


def compute_value_density_ratio(html: str) -> Tuple[float, int, int]:
    """
    Returns: (ratio, total_tags, value_tags)
    """
    parser = _ValueDensityHTMLParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # If parsing fails, treat as no tags/value.
        return 0.0, 0, 0

    total = parser.total_tags
    value = parser.value_tags
    ratio = (value / total) if total > 0 else 0.0
    return ratio, total, value


def filter_top_n_by_value_density(
    input_path: str,
    output_path: str,
    top_n: int,
    min_total_tags: int = 0,
    require_html: bool = False,
    scores_output_path: str | None = None,
) -> dict:
    print(f"Loading JSON from: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected JSON to be an array of objects")

    total_entries = len(data)
    print(f"Total entries loaded: {total_entries}")

    scored: List[dict] = []
    skipped_no_gpt = 0
    skipped_min_tags = 0
    skipped_no_html = 0

    for i, entry in enumerate(data):
        if (i + 1) % 100 == 0:
            print(f"Scoring entry {i + 1}/{total_entries}...")

        gpt_response = get_gpt_response_from_entry(entry)
        if gpt_response is None:
            skipped_no_gpt += 1
            continue

        ratio, total_tags, value_tags = compute_value_density_ratio(str(gpt_response))

        if require_html and total_tags == 0:
            skipped_no_html += 1
            continue

        if total_tags < min_total_tags:
            skipped_min_tags += 1
            continue

        scored.append(
            {
                "entry": entry,
                "id": entry.get("id", i),
                "ratio": ratio,
                "total_tags": total_tags,
                "value_tags": value_tags,
                "index": i,
            }
        )

    scored.sort(key=lambda x: (-x["ratio"], -x["total_tags"], x["index"]))

    if top_n < 0:
        kept = scored
    else:
        kept = scored[: min(top_n, len(scored))]

    kept_entries = [x["entry"] for x in kept]

    print(f"\nSaving filtered data to: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(kept_entries, f, indent=2, ensure_ascii=False)

    if scores_output_path:
        print(f"Saving scores to: {scores_output_path}")
        with open(scores_output_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "id": x["id"],
                        "ratio": x["ratio"],
                        "total_tags": x["total_tags"],
                        "value_tags": x["value_tags"],
                        "index": x["index"],
                    }
                    for x in scored
                ],
                f,
                indent=2,
                ensure_ascii=False,
            )

    avg_ratio = (sum(x["ratio"] for x in kept) / len(kept)) if kept else 0.0
    stats = {
        "total_entries": total_entries,
        "scored_entries": len(scored),
        "kept_count": len(kept_entries),
        "top_n": top_n,
        "min_total_tags": min_total_tags,
        "require_html": require_html,
        "skipped_no_gpt": skipped_no_gpt,
        "skipped_below_min_total_tags": skipped_min_tags,
        "skipped_no_html": skipped_no_html,
        "average_ratio_of_kept": avg_ratio,
        "top_examples": [
            {
                "id": x["id"],
                "ratio": x["ratio"],
                "total_tags": x["total_tags"],
                "value_tags": x["value_tags"],
            }
            for x in kept[:10]
        ],
    }

    print("\n" + "=" * 60)
    print("VALUE DENSITY FILTER STATISTICS")
    print("=" * 60)
    print(f"Total entries:                 {stats['total_entries']}")
    print(f"Scored entries:                {stats['scored_entries']}")
    print(f"Kept entries:                  {stats['kept_count']}")
    print(f"Skipped (no GPT response):     {stats['skipped_no_gpt']}")
    print(f"Skipped (< min_total_tags):    {stats['skipped_below_min_total_tags']}")
    print(f"Skipped (no HTML tags):        {stats['skipped_no_html']}")
    print(f"Average ratio (kept):          {stats['average_ratio_of_kept']:.4f}")
    print("=" * 60)

    if kept:
        print("\nTop kept examples:")
        for x in kept[:10]:
            print(
                f"  - {x['id']}: ratio={x['ratio']:.4f} "
                f"(value_tags={x['value_tags']}, total_tags={x['total_tags']})"
            )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep top-N JSON entries ranked by HTML value density (value tags / total tags)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python filter_value_ratio.py --top-n 10000 --input /path/to/input.json --output /path/to/output.json
  python filter_value_ratio.py --top-n 5000 --min-total-tags 20 --require-html
  python filter_value_ratio.py --top-n -1 --scores-output /tmp/all_scores.json
        """.strip(),
    )
    parser.add_argument("--top-n", type=int, required=True, help="Number of samples to keep (use -1 to keep all)")
    parser.add_argument(
        "--min-total-tags",
        type=int,
        default=0,
        help="Only consider entries whose GPT HTML has at least this many tags (default: 0)",
    )
    parser.add_argument(
        "--require-html",
        action="store_true",
        help="If set, discard entries whose GPT response has 0 HTML tags",
    )
    parser.add_argument(
        "--input",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_read.json",
        help="Input JSON file path",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_read_topN_value_ratio.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--scores-output",
        type=str,
        default=None,
        help="Optional path to write per-entry scores JSON (all scored entries, sorted by rank)",
    )

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        return 1

    try:
        filter_top_n_by_value_density(
            input_path=args.input,
            output_path=args.output,
            top_n=args.top_n,
            min_total_tags=args.min_total_tags,
            require_html=args.require_html,
            scores_output_path=args.scores_output,
        )
        print(f"\nSuccess! Filtered data saved to: {args.output}")
        return 0
    except Exception as e:
        print(f"\nError during filtering: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

