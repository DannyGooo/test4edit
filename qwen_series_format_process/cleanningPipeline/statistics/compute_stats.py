#!/usr/bin/env python3
"""
Compute image and text statistics for a tar-based dataset.

Streams entries from a JSON metadata file, extracts images from tar archives,
and collects per-entry statistics (dimensions, file size, pixel std, channels,
mode, token/char counts, corruption flag). Writes stats.jsonl (per-entry) and
summary.json (aggregates).
"""

import argparse
import io
import json
import os
import sys
import tarfile
import time

import ijson
import numpy as np
from pathlib import Path
from PIL import Image


# ---------------------------------------------------------------------------
# Tar caching (adapted from filter_images.py)
# ---------------------------------------------------------------------------
_current_tar_name = None
_current_tar_handle = None


def _get_tar_handle(tars_dir, shard):
    global _current_tar_name, _current_tar_handle
    if _current_tar_name != shard:
        if _current_tar_handle is not None:
            _current_tar_handle.close()
        tar_path = os.path.join(tars_dir, shard)
        _current_tar_handle = tarfile.open(tar_path, "r")
        _current_tar_name = shard
    return _current_tar_handle


def _close_tar_handle():
    global _current_tar_name, _current_tar_handle
    if _current_tar_handle is not None:
        _current_tar_handle.close()
        _current_tar_handle = None
        _current_tar_name = None


def get_image_bytes_from_tar(tars_dir, shard, image_name):
    tf = _get_tar_handle(tars_dir, shard)
    member = tf.extractfile(image_name)
    if member is None:
        raise FileNotFoundError(f"{image_name} not found in {shard}")
    return member.read()


# ---------------------------------------------------------------------------
# Per-entry stat collection
# ---------------------------------------------------------------------------

def compute_entry_stats(entry, tars_dir, tokenizer):
    """Compute stats for a single entry. Returns a dict or None on skip."""
    entry_id = entry.get("id")
    shard = entry.get("shard")
    image_name = entry.get("image_in_tar")

    if not shard or not image_name:
        return None

    stat = {
        "id": entry_id,
        "shard": shard,
        "image_in_tar": image_name,
        "corrupted": False,
        "corruption_reason": "",
        "image_width": None,
        "image_height": None,
        "image_file_size": None,
        "pixel_std": None,
        "image_channels": None,
        "image_mode": None,
        "gpt_token_count": None,
        "gpt_char_count": None,
    }

    # --- Image stats ---
    try:
        data = get_image_bytes_from_tar(tars_dir, shard, image_name)
    except Exception as e:
        stat["corrupted"] = True
        stat["corruption_reason"] = f"tar read error: {e}"
        return stat

    stat["image_file_size"] = len(data)

    # verify pass
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception as e:
        stat["corrupted"] = True
        stat["corruption_reason"] = f"verify failed: {e}"
        return stat

    # load pass (verify consumes the image, reopen)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        stat["corrupted"] = True
        stat["corruption_reason"] = f"load failed: {e}"
        return stat

    stat["image_width"] = img.size[0]
    stat["image_height"] = img.size[1]
    stat["image_mode"] = img.mode
    stat["image_channels"] = len(img.getbands())

    try:
        arr = np.array(img)
        stat["pixel_std"] = float(np.std(arr))
    except Exception as e:
        stat["pixel_std"] = None

    # --- Text stats ---
    try:
        gpt_text = entry["conversations"][1]["value"]
    except (KeyError, IndexError, TypeError):
        gpt_text = ""

    stat["gpt_char_count"] = len(gpt_text)
    if tokenizer is not None and gpt_text:
        try:
            stat["gpt_token_count"] = len(tokenizer.encode(gpt_text))
        except Exception:
            stat["gpt_token_count"] = None

    return stat


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def compute_summary(stats_path):
    """Read stats.jsonl and compute aggregate summary."""
    numeric_keys = [
        "image_width", "image_height", "image_file_size",
        "pixel_std", "image_channels",
        "gpt_token_count", "gpt_char_count",
    ]
    accum = {k: [] for k in numeric_keys}
    mode_counts = {}
    total = 0
    corrupted = 0

    with open(stats_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            total += 1
            if rec.get("corrupted"):
                corrupted += 1

            for k in numeric_keys:
                v = rec.get(k)
                if v is not None:
                    accum[k].append(v)

            mode = rec.get("image_mode")
            if mode is not None:
                mode_counts[mode] = mode_counts.get(mode, 0) + 1

    summary = {
        "total_entries": total,
        "corrupted_entries": corrupted,
        "valid_entries": total - corrupted,
        "image_mode_counts": mode_counts,
    }

    percentiles = [1, 5, 25, 50, 75, 95, 99]
    for k in numeric_keys:
        vals = accum[k]
        if not vals:
            summary[k] = {"count": 0}
            continue
        arr = np.array(vals, dtype=np.float64)
        summary[k] = {
            "count": len(vals),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "std": float(np.std(arr)),
        }
        for p in percentiles:
            summary[k][f"p{p}"] = float(np.percentile(arr, p))

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compute image and text statistics for a tar-based dataset",
    )
    parser.add_argument("--input", type=str, required=True, help="Input JSON metadata file")
    parser.add_argument("--tars-dir", type=str, required=True, help="Directory with images-*.tar files")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory to write stats.jsonl and summary.json")
    parser.add_argument("--tokenizer", type=str, default="Qwen/Qwen2.5-VL-7B-Instruct", help="HuggingFace tokenizer name")

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        return 1
    if not Path(args.tars_dir).is_dir():
        print(f"Error: Tars directory not found: {args.tars_dir}")
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    stats_path = os.path.join(args.output_dir, "stats.jsonl")
    summary_path = os.path.join(args.output_dir, "summary.json")

    # Load tokenizer
    print(f"Loading tokenizer: {args.tokenizer}")
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, trust_remote_code=True)
    except Exception as e:
        print(f"Warning: Could not load tokenizer ({e}). Token counts will be skipped.")
        tokenizer = None

    # Stream entries and collect stats
    print(f"Streaming entries from: {args.input}")
    print(f"Writing per-entry stats to: {stats_path}")

    total = 0
    skipped = 0
    t0 = time.time()

    with open(args.input, "rb") as fin, open(stats_path, "w") as fout:
        for entry in ijson.items(fin, "item"):
            total += 1
            if total % 5000 == 0:
                elapsed = time.time() - t0
                print(f"  processed {total} entries ({elapsed:.1f}s)")

            stat = compute_entry_stats(entry, args.tars_dir, tokenizer)
            if stat is None:
                skipped += 1
                continue

            fout.write(json.dumps(stat, ensure_ascii=False) + "\n")

    _close_tar_handle()

    elapsed = time.time() - t0
    print(f"\nDone streaming. {total} entries processed, {skipped} skipped. ({elapsed:.1f}s)")

    # Compute aggregate summary
    print("Computing aggregate summary...")
    summary = compute_summary(stats_path)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print summary to stdout
    print("\n" + "=" * 60)
    print("DATASET STATISTICS SUMMARY")
    print("=" * 60)
    print(f"Total entries:     {summary['total_entries']}")
    print(f"Corrupted:         {summary['corrupted_entries']}")
    print(f"Valid:             {summary['valid_entries']}")
    print(f"\nImage mode distribution:")
    for mode, count in sorted(summary["image_mode_counts"].items(), key=lambda x: -x[1]):
        print(f"  {mode:6s}: {count}")

    numeric_keys = [
        "image_width", "image_height", "image_file_size",
        "pixel_std", "image_channels",
        "gpt_token_count", "gpt_char_count",
    ]
    for k in numeric_keys:
        info = summary[k]
        if info.get("count", 0) == 0:
            continue
        print(f"\n{k}:")
        print(f"  count={info['count']}  min={info['min']:.2f}  max={info['max']:.2f}")
        print(f"  mean={info['mean']:.2f}  median={info['median']:.2f}  std={info['std']:.2f}")
        print(f"  p1={info['p1']:.2f}  p5={info['p5']:.2f}  p25={info['p25']:.2f}  p50={info['p50']:.2f}  p75={info['p75']:.2f}  p95={info['p95']:.2f}  p99={info['p99']:.2f}")

    print("=" * 60)
    print(f"\nResults written to:")
    print(f"  {stats_path}")
    print(f"  {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
