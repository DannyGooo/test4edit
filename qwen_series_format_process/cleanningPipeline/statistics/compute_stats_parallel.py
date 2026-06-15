#!/usr/bin/env python3
"""
Parallel version of compute_stats.py.

Groups entries by shard, then uses multiprocessing.Pool so each worker opens
its tar file once, processes all entries for that shard, and returns stat dicts.
"""

import argparse
import io
import json
import os
import sys
import tarfile
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import ijson
import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Worker initializer — each process loads its own tokenizer once
# ---------------------------------------------------------------------------
_worker_tokenizer = None


def _init_worker(tokenizer_name):
    global _worker_tokenizer
    if tokenizer_name:
        try:
            from transformers import AutoTokenizer
            _worker_tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_name, trust_remote_code=True
            )
        except Exception as e:
            print(f"[worker {os.getpid()}] tokenizer load failed: {e}", flush=True)
            _worker_tokenizer = None


# ---------------------------------------------------------------------------
# Per-entry stat collection (takes an open tar handle directly)
# ---------------------------------------------------------------------------

def compute_entry_stats_from_tar(entry, tf, tokenizer):
    """Compute stats for a single entry using an already-open tar handle."""
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
        member = tf.extractfile(image_name)
        if member is None:
            raise FileNotFoundError(f"{image_name} not found in {shard}")
        data = member.read()
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
    except Exception:
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
# Worker function — process all entries for one shard
# ---------------------------------------------------------------------------

def process_shard_group(args):
    """Open tar once, process all entries for this shard, return stat dicts."""
    shard, entries, tars_dir = args
    tar_path = os.path.join(tars_dir, shard)
    results = []

    try:
        tf = tarfile.open(tar_path, "r")
    except Exception as e:
        # If we can't open the tar, mark all entries as corrupted
        for entry in entries:
            results.append({
                "id": entry.get("id"),
                "shard": shard,
                "image_in_tar": entry.get("image_in_tar"),
                "corrupted": True,
                "corruption_reason": f"tar open error: {e}",
                "image_width": None,
                "image_height": None,
                "image_file_size": None,
                "pixel_std": None,
                "image_channels": None,
                "image_mode": None,
                "gpt_token_count": None,
                "gpt_char_count": None,
            })
        return results

    try:
        for entry in entries:
            stat = compute_entry_stats_from_tar(entry, tf, _worker_tokenizer)
            if stat is not None:
                results.append(stat)
    finally:
        tf.close()

    return results


# ---------------------------------------------------------------------------
# Aggregation (reused from compute_stats.py)
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
        description="Compute image and text statistics (parallel, grouped by shard)",
    )
    parser.add_argument("--input", type=str, required=True, help="Input JSON metadata file")
    parser.add_argument("--tars-dir", type=str, required=True, help="Directory with images-*.tar files")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory to write stats.jsonl and summary.json")
    parser.add_argument("--tokenizer", type=str, default="Qwen/Qwen2.5-VL-7B-Instruct", help="HuggingFace tokenizer name")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 16),
                        help="Number of parallel workers (default: min(cpu_count, 16))")

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

    # ---- Phase 1: Stream entries and group by shard ----
    print(f"Streaming entries from: {args.input}")
    t0 = time.time()
    shard_groups = defaultdict(list)
    total_entries = 0

    with open(args.input, "rb") as fin:
        for entry in ijson.items(fin, "item"):
            total_entries += 1
            shard = entry.get("shard")
            if shard:
                shard_groups[shard].append(entry)
            if total_entries % 10000 == 0:
                print(f"  loaded {total_entries} entries ...", flush=True)

    elapsed_load = time.time() - t0
    num_shards = len(shard_groups)
    print(f"Loaded {total_entries} entries across {num_shards} shards ({elapsed_load:.1f}s)")

    # ---- Phase 2: Parallel processing ----
    work_items = [
        (shard, entries, args.tars_dir)
        for shard, entries in shard_groups.items()
    ]

    print(f"Processing with {args.workers} workers across {num_shards} shards...")
    t1 = time.time()

    written = 0
    with Pool(args.workers, initializer=_init_worker, initargs=(args.tokenizer,)) as pool, \
         open(stats_path, "w") as fout:
        for i, shard_results in enumerate(pool.imap_unordered(process_shard_group, work_items)):
            for stat in shard_results:
                fout.write(json.dumps(stat, ensure_ascii=False) + "\n")
                written += 1
            if (i + 1) % 20 == 0 or (i + 1) == num_shards:
                print(f"  completed {i + 1}/{num_shards} shards ({written} entries written)", flush=True)

    elapsed_proc = time.time() - t1
    elapsed_total = time.time() - t0
    print(f"\nDone. {written} entries written. "
          f"Processing: {elapsed_proc:.1f}s, Total: {elapsed_total:.1f}s")

    # ---- Phase 3: Compute aggregate summary ----
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
