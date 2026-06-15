#!/usr/bin/env python3
"""
VinciCoder (web2html) parquet -> MS-Swift JSONL format converter.

Input parquet schema (expected):
  - user_content: string
  - assistant_content: string
  - image: binary (PNG bytes)

Output structure:
  {output_dir}/ms_swift/{category_name}/
      images-00000.tar, images-00001.tar, ...
      meta_data_{prefix}.jsonl              # original prompts from parquet
      meta_data_{prefix}_fixed.jsonl        # fixed prompt template
      meta_data_{prefix}_100k.jsonl         # 100k subset (original)
      meta_data_{prefix}_fixed_100k.jsonl   # 100k subset (fixed)

Each JSONL line has the shape:
  {"messages": [{"role": "user", "content": "..."},
                {"role": "assistant", "content": "<html>..."}],
   "images": ["images-00000.tar/vincicoder_000000.png"]}

Examples:
  python vincicoder_parquet_to_ms_swift.py \
    -o ./output \
    --inputs /path/web2html_1.parquet /path/web2html_2.parquet

  # Export only 100 samples
  python vincicoder_parquet_to_ms_swift.py -o ./output --inputs ... -n 100

  # Verify existing output
  python vincicoder_parquet_to_ms_swift.py -o ./output --inputs ... --verify
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tar_shard_writer import (
    TarShardWriter,
    count_tar_png_members,
    scan_present_indices,
    validate_and_repair_shards,
    verify_shard_sequence,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


HUMAN_PROMPT_FIXED = """<image>
Drawing from the webpage screenshot, create corresponding HTML and CSS code.
"""


_CODE_FENCE_RE = re.compile(r"```(?:html)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_HTML_START_RE = re.compile(r"^\s*(<html|<!DOCTYPE\s+html)", re.IGNORECASE)
_HTML_END_RE = re.compile(r"</html>\s*$", re.IGNORECASE)


def _extract_code_from_markdown(text: str) -> str:
    """VinciCoder assistant_content often wraps HTML in ```html ... ``` fences.
    Extract the first fenced block if present; otherwise return stripped text.
    """
    text = (text or "").strip()
    if not text:
        return ""
    m = _CODE_FENCE_RE.search(text)
    if m:
        return (m.group(1) or "").strip()
    return text


def _as_bytes(v: Any) -> Optional[bytes]:
    if v is None:
        return None
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    if isinstance(v, memoryview):
        return v.tobytes()
    return None


@dataclass
class ConverterConfig:
    output_dir: Path
    inputs: List[Path]
    category_name: str
    prefix: str
    batch_size: int
    checkpoint_interval: int
    max_samples: Optional[int]
    validate_html: bool
    resume: bool
    scan_existing_tars: bool = False


class VinciCoderParquetToMSSwift:
    def __init__(self, cfg: ConverterConfig):
        self.cfg = cfg

        self.base_dir = cfg.output_dir / "ms_swift" / cfg.category_name
        self.tar_writer = TarShardWriter(self.base_dir)

        self.checkpoint_file = self.base_dir / f".{cfg.prefix}_checkpoint.json"
        self.metadata_file = self.base_dir / f"meta_data_{cfg.prefix}.jsonl"
        self.metadata_fixed_file = self.base_dir / f"meta_data_{cfg.prefix}_fixed.jsonl"
        self.metadata_100k_file = self.base_dir / f"meta_data_{cfg.prefix}_100k.jsonl"
        self.metadata_fixed_100k_file = self.base_dir / f"meta_data_{cfg.prefix}_fixed_100k.jsonl"

        self.exported_count = 0
        self.processed_rows = 0
        self.parquet_idx = 0
        self.parquet_row_idx = 0
        self.present_indices: Dict[int, str] = {}

        self._metadata_stream = None
        self._metadata_fixed_stream = None

        self.stats = {
            "total_processed": 0,
            "total_exported": 0,
            "skipped_missing_image": 0,
            "skipped_missing_code": 0,
            "skipped_invalid_html": 0,
            "skipped_errors": 0,
            "start_time": None,
        }

        self._original_sigint = None
        self._original_sigterm = None

    def _setup_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _format_file_id(self, num: int) -> str:
        return f"{self.cfg.prefix}_{num:06d}"

    def _setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, saving checkpoint...")
            self.tar_writer.close()
            self._close_streams()
            self._save_checkpoint()
            self._restore_signal_handlers()
            sys.exit(0)

        self._original_sigint = signal.signal(signal.SIGINT, handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, handler)

    def _restore_signal_handlers(self) -> None:
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)

    def _load_checkpoint(self) -> bool:
        if not self.checkpoint_file.exists():
            return False
        try:
            checkpoint = json.loads(self.checkpoint_file.read_text())
            self.exported_count = int(checkpoint.get("exported_count", 0))
            self.processed_rows = int(checkpoint.get("processed_rows", 0))
            self.parquet_idx = int(checkpoint.get("parquet_idx", 0))
            self.parquet_row_idx = int(checkpoint.get("parquet_row_idx", 0))
            self.stats = checkpoint.get("stats", self.stats)
            logger.info(
                "Resumed from checkpoint: exported=%s processed_rows=%s parquet_idx=%s parquet_row_idx=%s",
                self.exported_count,
                self.processed_rows,
                self.parquet_idx,
                self.parquet_row_idx,
            )
            return True
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)
            return False

    def _save_checkpoint(self) -> None:
        payload = {
            "exported_count": self.exported_count,
            "processed_rows": self.processed_rows,
            "parquet_idx": self.parquet_idx,
            "parquet_row_idx": self.parquet_row_idx,
            "stats": self.stats,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file.write_text(json.dumps(payload, indent=2))

    def _init_streams(self, append: bool) -> None:
        """Open both JSONL metadata streams (original + fixed prompt variants)."""
        for metadata_file, attr_name in [
            (self.metadata_file, "_metadata_stream"),
            (self.metadata_fixed_file, "_metadata_fixed_stream"),
        ]:
            mode = "a" if (append and metadata_file.exists()) else "w"
            stream = open(metadata_file, mode, encoding="utf-8")
            setattr(self, attr_name, stream)

    def _append_entries(self, entry_original: Dict[str, Any], entry_fixed: Dict[str, Any]) -> None:
        if self._metadata_stream:
            self._metadata_stream.write(json.dumps(entry_original, ensure_ascii=False) + "\n")
            self._metadata_stream.flush()
        if self._metadata_fixed_stream:
            self._metadata_fixed_stream.write(json.dumps(entry_fixed, ensure_ascii=False) + "\n")
            self._metadata_fixed_stream.flush()

    def _close_streams(self) -> None:
        for attr in ["_metadata_stream", "_metadata_fixed_stream"]:
            stream = getattr(self, attr, None)
            if stream:
                stream.close()
                setattr(self, attr, None)

    def _is_valid_html(self, html: str) -> bool:
        if not html or not isinstance(html, str):
            return False
        return bool(_HTML_START_RE.match(html)) and bool(_HTML_END_RE.search(html))

    def _read_parquet_batches(self, path: Path) -> Iterable[Tuple[List[Any], List[Any], List[Any]]]:
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        for record_batch in pf.iter_batches(batch_size=self.cfg.batch_size, columns=["user_content", "assistant_content", "image"]):
            cols = record_batch.to_pydict()
            yield (cols.get("user_content", []), cols.get("assistant_content", []), cols.get("image", []))

    def _create_100k_subsets(self) -> None:
        """Create 100k subset JSONL files for both original and fixed versions."""
        if self.exported_count <= 100000:
            import shutil

            if self.metadata_file.exists():
                shutil.copy(self.metadata_file, self.metadata_100k_file)
            if self.metadata_fixed_file.exists():
                shutil.copy(self.metadata_fixed_file, self.metadata_fixed_100k_file)
            return

        for src, dst in [
            (self.metadata_file, self.metadata_100k_file),
            (self.metadata_fixed_file, self.metadata_fixed_100k_file),
        ]:
            try:
                count = 0
                with open(src, "r", encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
                    for line in fin:
                        if count >= 100000:
                            break
                        fout.write(line)
                        count += 1
            except Exception as e:
                logger.warning("Failed to write 100k subset for %s: %s", src.name, e)

    def run(self) -> Dict[str, Any]:
        self.stats["start_time"] = time.time()
        self._setup_signal_handlers()
        self._setup_directories()

        if self.cfg.scan_existing_tars:
            logger.info("Validating and repairing existing tar shards...")
            validate_and_repair_shards(self.base_dir, self.cfg.prefix)
            logger.info("Scanning existing tar shards for already-exported samples...")
            self.present_indices = scan_present_indices(self.base_dir, self.cfg.prefix)
            if self.present_indices:
                idxs = sorted(self.present_indices.keys())
                logger.info(
                    "Found %s existing PNGs across tars (min=%s, max=%s)",
                    f"{len(idxs):,}",
                    idxs[0],
                    idxs[-1],
                )
            else:
                logger.info("No existing tar shards found; will fetch all samples from scratch.")
            self.exported_count = 0
            self.processed_rows = 0
            self.parquet_idx = 0
            self.parquet_row_idx = 0
            is_resuming = False
        else:
            is_resuming = self.cfg.resume and self._load_checkpoint()
        resume_parquet_idx = self.parquet_idx if is_resuming else 0
        resume_parquet_row_idx = self.parquet_row_idx if is_resuming else 0
        self._init_streams(append=is_resuming)

        logger.info("VinciCoder parquet -> MS-Swift")
        logger.info("  output: %s", self.base_dir)
        logger.info("  inputs: %s", ", ".join(str(p) for p in self.cfg.inputs))
        logger.info("  max_samples: %s", self.cfg.max_samples or "all")
        logger.info("  validate_html: %s", self.cfg.validate_html)
        logger.info("  scan_existing_tars: %s", self.cfg.scan_existing_tars)
        if is_resuming and resume_parquet_idx > 0:
            logger.info(
                "Resume: skipping first %d parquet file(s) entirely; resuming inside %s at row %d",
                resume_parquet_idx,
                self.cfg.inputs[resume_parquet_idx] if resume_parquet_idx < len(self.cfg.inputs) else "<end>",
                resume_parquet_row_idx,
            )

        completed_successfully = False
        try:
            for pq_idx, parquet_path in enumerate(self.cfg.inputs):
                if pq_idx < resume_parquet_idx:
                    continue

                self.parquet_idx = pq_idx
                logger.info("Processing %s", parquet_path)

                # Only the parquet at resume_parquet_idx needs row-level skip
                row_skip = resume_parquet_row_idx if pq_idx == resume_parquet_idx else 0
                self.parquet_row_idx = row_skip

                local_row_idx = 0
                for users, assistants, images in self._read_parquet_batches(parquet_path):
                    for user_content, assistant_content, image_val in zip(users, assistants, images):
                        if local_row_idx < row_skip:
                            local_row_idx += 1
                            continue

                        self.stats["total_processed"] += 1
                        local_row_idx += 1
                        self.parquet_row_idx = local_row_idx
                        self.processed_rows += 1

                        if self.cfg.max_samples is not None and self.exported_count >= self.cfg.max_samples:
                            raise StopIteration

                        img_bytes = _as_bytes(image_val)
                        if not img_bytes:
                            self.stats["skipped_missing_image"] += 1
                            continue

                        html = _extract_code_from_markdown(str(assistant_content or ""))
                        if not html.strip():
                            self.stats["skipped_missing_code"] += 1
                            continue

                        if self.cfg.validate_html and not self._is_valid_html(html):
                            self.stats["skipped_invalid_html"] += 1
                            continue

                        try:
                            original_prompt = str(user_content or "").strip()

                            existing_path = (
                                self.present_indices.get(self.exported_count)
                                if self.cfg.scan_existing_tars
                                else None
                            )
                            if existing_path is not None:
                                image_ref = [existing_path]
                                self.stats["reused_existing"] = self.stats.get("reused_existing", 0) + 1
                            else:
                                file_id = self._format_file_id(self.exported_count)
                                image_ref = [self.tar_writer.add_image(file_id, img_bytes, self.exported_count)]

                            entry_original = {
                                "messages": [
                                    {"role": "user", "content": original_prompt},
                                    {"role": "assistant", "content": html},
                                ],
                                "images": image_ref,
                            }
                            entry_fixed = {
                                "messages": [
                                    {"role": "user", "content": HUMAN_PROMPT_FIXED},
                                    {"role": "assistant", "content": html},
                                ],
                                "images": image_ref,
                            }
                            self._append_entries(entry_original, entry_fixed)

                            self.exported_count += 1
                            self.stats["total_exported"] += 1

                        except Exception:
                            self.stats["skipped_errors"] += 1
                            continue

                        if self.processed_rows % self.cfg.checkpoint_interval == 0:
                            self._save_checkpoint()

                # Finished this parquet; the next one starts at row 0
                self.parquet_row_idx = 0

        except StopIteration:
            logger.info("Reached export limit: %s", self.cfg.max_samples)
            completed_successfully = True
        except Exception:
            raise
        else:
            completed_successfully = True
        finally:
            self.tar_writer.close()
            self._close_streams()
            self._save_checkpoint()
            self._create_100k_subsets()
            if completed_successfully and self.cfg.max_samples is None and self.checkpoint_file.exists():
                try:
                    self.checkpoint_file.unlink()
                except Exception:
                    pass
            self._restore_signal_handlers()

            issues = verify_shard_sequence(self.base_dir, self.cfg.prefix)
            if issues:
                logger.warning("Shard sequence check found issues:")
                for msg in issues:
                    logger.warning("  - %s", msg)
            else:
                logger.info(
                    "Shard sequence check passed: each tar has 5000 sequential members (last shard may be partial)."
                )

        elapsed = time.time() - (self.stats["start_time"] or time.time())
        rate = self.stats["total_exported"] / elapsed if elapsed > 0 else 0.0
        logger.info(
            "Done. processed=%s exported=%s skipped=%s rate=%.2f/s",
            self.stats["total_processed"],
            self.stats["total_exported"],
            self.stats["total_processed"] - self.stats["total_exported"],
            rate,
        )
        return self.stats

    def verify_output(self) -> Dict[str, Any]:
        stats = {
            "total_images": 0,
            "metadata_entries": 0,
            "metadata_fixed_entries": 0,
            "metadata_valid": False,
            "metadata_fixed_valid": False,
            "mismatched_files": [],
            "sample_entries": [],
        }

        if self.base_dir.exists():
            stats["total_images"] = count_tar_png_members(self.base_dir)

        ok, n, sample = _count_jsonl(self.metadata_file, sample_n=3)
        stats["metadata_valid"] = ok
        stats["metadata_entries"] = n if ok else -1
        stats["sample_entries"] = sample

        ok_f, n_f, _ = _count_jsonl(self.metadata_fixed_file, sample_n=0)
        stats["metadata_fixed_valid"] = ok_f
        stats["metadata_fixed_entries"] = n_f if ok_f else -1

        if ok and n != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Metadata entries ({n}) != image count ({stats['total_images']})"
            )
        if ok_f and n_f != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Fixed metadata entries ({n_f}) != image count ({stats['total_images']})"
            )

        if stats["metadata_valid"]:
            for i, entry in enumerate(stats["sample_entries"]):
                msgs = entry.get("messages")
                if not isinstance(msgs, list) or len(msgs) != 2:
                    stats["mismatched_files"].append(f"Entry #{i} messages malformed")
                imgs = entry.get("images")
                if not isinstance(imgs, list) or not imgs:
                    stats["mismatched_files"].append(f"Entry #{i} images missing/empty")

        return stats


def _count_jsonl(path: Path, sample_n: int = 3) -> Tuple[bool, int, List[Dict[str, Any]]]:
    if not path.exists():
        return False, 0, []
    try:
        count = 0
        samples: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                if len(samples) < sample_n:
                    samples.append(json.loads(line))
        return True, count, samples
    except Exception:
        return False, -1, []


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert VinciCoder web2html parquets to MS-Swift JSONL format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=str,
        required=True,
        help="Base output directory (required). Writes into {output_dir}/ms_swift/{category_name}/",
    )
    p.add_argument(
        "--inputs",
        type=str,
        nargs="+",
        required=True,
        help="Input parquet paths (one or more).",
    )
    p.add_argument(
        "--category-name",
        type=str,
        default="vincicoder",
        help="Output category folder under ms_swift (default: vincicoder).",
    )
    p.add_argument(
        "--prefix",
        type=str,
        default="vincicoder",
        help="Image filename prefix and metadata prefix (default: vincicoder).",
    )
    p.add_argument(
        "-n",
        "--num-samples",
        type=int,
        default=None,
        help="Maximum number of samples to export (default: all).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Parquet batch size (default: 128).",
    )
    p.add_argument(
        "--checkpoint-interval",
        type=int,
        default=2000,
        help="Save checkpoint every N processed rows (default: 2000).",
    )
    p.add_argument(
        "--validate-html",
        action="store_true",
        help="Drop samples whose extracted HTML doesn't start with <html>/<!DOCTYPE html> or end with </html>.",
    )
    p.add_argument("--resume", action="store_true", help="Resume from checkpoint if it exists.")
    p.add_argument("--no-resume", action="store_true", help="Ignore checkpoint and start fresh.")
    p.add_argument(
        "--scan-tars",
        action="store_true",
        help=(
            "Scan existing tar shards and only fetch samples whose {prefix}_NNNNNN.png is missing. "
            "Iterates inputs from the start, rebuilds the metadata jsonl to match all on-disk tars, "
            "and auto-repairs corrupt shards."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    p.add_argument("--verify", action="store_true", help="Verify output files and exit.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = ConverterConfig(
        output_dir=Path(args.output_dir),
        inputs=[Path(p) for p in args.inputs],
        category_name=args.category_name,
        prefix=args.prefix,
        batch_size=args.batch_size,
        checkpoint_interval=args.checkpoint_interval,
        max_samples=args.num_samples,
        validate_html=args.validate_html,
        resume=(args.resume or (not args.no_resume)),
        scan_existing_tars=args.scan_tars,
    )

    converter = VinciCoderParquetToMSSwift(cfg)
    if args.verify:
        print(json.dumps(converter.verify_output(), indent=2))
        return

    try:
        converter.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error("Fatal error: %s", e)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
