#!/usr/bin/env python3
"""
VinciCoder (web2html) parquet -> Qwen Series format converter.

Input parquet schema (expected):
  - user_content: string
  - assistant_content: string
  - image: binary (PNG bytes)

Output structure:
  {output_dir}/qwen_series/{category_name}/
      images/
          {prefix}_000000.png, {prefix}_000001.png, ...
      meta_data_{prefix}.json              # original prompts from parquet
      meta_data_{prefix}_fixed.json        # fixed prompt template
      meta_data_{prefix}_100k.json         # 100k subset (original)
      meta_data_{prefix}_fixed_100k.json   # 100k subset (fixed)

Examples:
  python vincicoder_parquet_to_qwen_series.py \
    -o ./output \
    --inputs /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/vincicoder/web2html_1.parquet \
             /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/vincicoder/web2html_2.parquet

  # Export only 100 samples
  python vincicoder_parquet_to_qwen_series.py -o ./output --inputs ... -n 100

  # Verify existing output
  python vincicoder_parquet_to_qwen_series.py -o ./output --inputs ... --verify
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


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


HUMAN_PROMPT_FIXED = """<image>
You are an expert web developer who specializes in HTML and CSS. Given a screenshot of a reference webpage, build a pixel-perfect single-page app using only HTML and CSS.

- Make sure the app looks exactly like the screenshot.
- Pay close attention to background color, text color, font size, font family, padding, margin, border, etc. Match the colors, layouts, and sizes exactly.
- Use the exact text from the screenshot.
- Do not add comments in the code such as "<!-- Add other navigation links as needed -->" and "<!-- ... other news items ... -->" in place of writing the full code. WRITE THE FULL CODE.
- Repeat elements as needed to match the screenshot. For example, if there are 15 items, the code should have 15 items. DO NOT LEAVE comments like "<!-- Repeat for each news item -->" or bad things will happen.
- For images, use placeholder images from https://placehold.co like https://placehold.co/300x200 so that the placeholder can replaced with the image later.

Deliver only the file contents (HTML with embedded <style>).
Do not include markdown "```" or "```html" at the start or end."""


_CODE_FENCE_RE = re.compile(r"```(?:html)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_HTML_START_RE = re.compile(r"^\s*(<html|<!DOCTYPE\s+html)", re.IGNORECASE)
_HTML_END_RE = re.compile(r"</html>\s*$", re.IGNORECASE)


def _extract_code_from_markdown(text: str) -> str:
    """
    VinciCoder assistant_content often wraps HTML in ```html ... ``` fences.
    This extracts the first fenced block if present; otherwise returns stripped text.
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
    # pyarrow can yield memoryview
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


class VinciCoderParquetToQwenSeries:
    def __init__(self, cfg: ConverterConfig):
        self.cfg = cfg

        self.base_dir = cfg.output_dir / "qwen_series" / cfg.category_name
        self.images_dir = self.base_dir / "images"

        self.checkpoint_file = self.base_dir / f".{cfg.prefix}_checkpoint.json"
        self.metadata_file = self.base_dir / f"meta_data_{cfg.prefix}.json"
        self.metadata_fixed_file = self.base_dir / f"meta_data_{cfg.prefix}_fixed.json"
        self.metadata_100k_file = self.base_dir / f"meta_data_{cfg.prefix}_100k.json"
        self.metadata_fixed_100k_file = self.base_dir / f"meta_data_{cfg.prefix}_fixed_100k.json"

        self.exported_count = 0
        self.processed_rows = 0  # total rows read across all input parquets
        self.is_first_entry = True

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
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def _format_file_id(self, num: int) -> str:
        return f"{self.cfg.prefix}_{num:06d}"

    def _setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, saving checkpoint...")
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
            self.stats = checkpoint.get("stats", self.stats)
            self.is_first_entry = False
            logger.info(
                "Resumed from checkpoint: exported=%s processed_rows=%s",
                self.exported_count,
                self.processed_rows,
            )
            return True
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)
            return False

    def _save_checkpoint(self) -> None:
        payload = {
            "exported_count": self.exported_count,
            "processed_rows": self.processed_rows,
            "stats": self.stats,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file.write_text(json.dumps(payload, indent=2))

    def _init_streams(self, append: bool) -> None:
        for metadata_file, attr_name in [
            (self.metadata_file, "_metadata_stream"),
            (self.metadata_fixed_file, "_metadata_fixed_stream"),
        ]:
            if append and metadata_file.exists():
                trimmed = metadata_file.read_text().rstrip()
                if trimmed.endswith("]"):
                    metadata_file.write_text(trimmed[:-1])
                stream = open(metadata_file, "a", encoding="utf-8")
                setattr(self, attr_name, stream)
            else:
                stream = open(metadata_file, "w", encoding="utf-8")
                stream.write("[\n")
                setattr(self, attr_name, stream)

        if not append:
            self.is_first_entry = True

    def _append_entries(self, entry_original: Dict[str, Any], entry_fixed: Dict[str, Any]) -> None:
        prefix = "" if self.is_first_entry else ",\n"
        if self._metadata_stream:
            self._metadata_stream.write(prefix + json.dumps(entry_original, indent=2))
        if self._metadata_fixed_stream:
            self._metadata_fixed_stream.write(prefix + json.dumps(entry_fixed, indent=2))
        self.is_first_entry = False

    def _close_streams(self) -> None:
        for attr in ["_metadata_stream", "_metadata_fixed_stream"]:
            stream = getattr(self, attr, None)
            if stream:
                stream.write("\n]\n")
                stream.close()
                setattr(self, attr, None)

    def _is_valid_html(self, html: str) -> bool:
        if not html or not isinstance(html, str):
            return False
        return bool(_HTML_START_RE.match(html)) and bool(_HTML_END_RE.search(html))

    def _read_parquet_batches(self, path: Path) -> Iterable[Tuple[List[Any], List[Any], List[Any]]]:
        """
        Yields batches as python lists: (user_content, assistant_content, image_bytes).
        """
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        for record_batch in pf.iter_batches(batch_size=self.cfg.batch_size, columns=["user_content", "assistant_content", "image"]):
            cols = record_batch.to_pydict()
            yield (cols.get("user_content", []), cols.get("assistant_content", []), cols.get("image", []))

    def _create_100k_subsets(self) -> None:
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
                data = json.loads(src.read_text())
                dst.write_text(json.dumps(data[:100000], indent=2))
            except Exception as e:
                logger.warning("Failed to write 100k subset for %s: %s", src.name, e)

    def run(self) -> Dict[str, Any]:
        self.stats["start_time"] = time.time()
        self._setup_signal_handlers()
        self._setup_directories()

        is_resuming = self.cfg.resume and self._load_checkpoint()
        # We resume by re-iterating inputs and skipping the first N rows.
        # This is robust but can be slow if N is huge.
        skip_until_row = self.processed_rows if is_resuming else 0
        self._init_streams(append=is_resuming)

        logger.info("VinciCoder parquet -> Qwen Series")
        logger.info("  output: %s", self.base_dir)
        logger.info("  inputs: %s", ", ".join(str(p) for p in self.cfg.inputs))
        logger.info("  max_samples: %s", self.cfg.max_samples or "all")
        logger.info("  validate_html: %s", self.cfg.validate_html)

        completed_successfully = False
        try:
            global_row_idx = 0
            for parquet_path in self.cfg.inputs:
                logger.info("Processing %s", parquet_path)
                for users, assistants, images in self._read_parquet_batches(parquet_path):
                    for user_content, assistant_content, image_val in zip(users, assistants, images):
                        # Resume skipping: advance global row index without re-exporting.
                        if is_resuming and global_row_idx < skip_until_row:
                            global_row_idx += 1
                            self.processed_rows = global_row_idx
                            continue

                        self.stats["total_processed"] += 1
                        global_row_idx += 1
                        self.processed_rows = global_row_idx

                        # export limit
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
                            file_id = self._format_file_id(self.exported_count)
                            out_img = self.images_dir / f"{file_id}.png"
                            out_img.write_bytes(img_bytes)

                            original_prompt = str(user_content or "").strip()
                            entry_original = {
                                "id": file_id,
                                "image": f"images/{file_id}.png",
                                "conversations": [
                                    {"from": "human", "value": original_prompt},
                                    {"from": "gpt", "value": html},
                                ],
                            }
                            entry_fixed = {
                                "id": file_id,
                                "image": f"images/{file_id}.png",
                                "conversations": [
                                    {"from": "human", "value": HUMAN_PROMPT_FIXED},
                                    {"from": "gpt", "value": html},
                                ],
                            }
                            self._append_entries(entry_original, entry_fixed)

                            self.exported_count += 1
                            self.stats["total_exported"] += 1

                        except Exception:
                            self.stats["skipped_errors"] += 1
                            continue

                        if self.processed_rows % self.cfg.checkpoint_interval == 0:
                            self._save_checkpoint()
                # Once we've passed the resume point, turn off resume mode.
                if is_resuming and global_row_idx >= skip_until_row:
                    is_resuming = False

        except StopIteration:
            logger.info("Reached export limit: %s", self.cfg.max_samples)
            completed_successfully = True
        except Exception:
            # Preserve checkpoint for debugging/resume.
            raise
        else:
            completed_successfully = True
        finally:
            self._close_streams()
            self._save_checkpoint()
            self._create_100k_subsets()
            # successful completion: remove checkpoint
            if completed_successfully and self.cfg.max_samples is None and self.checkpoint_file.exists():
                # only remove when processing full data without artificial limit
                try:
                    self.checkpoint_file.unlink()
                except Exception:
                    pass
            self._restore_signal_handlers()

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

        if self.images_dir.exists():
            stats["total_images"] = len(list(self.images_dir.glob("*.png")))

        def _load_json(path: Path) -> Tuple[bool, int, List[Dict[str, Any]]]:
            if not path.exists():
                return False, 0, []
            try:
                data = json.loads(path.read_text())
                sample = data[:3] if len(data) >= 3 else data
                return True, len(data), sample
            except Exception:
                return False, -1, []

        ok, n, sample = _load_json(self.metadata_file)
        stats["metadata_valid"] = ok
        stats["metadata_entries"] = n
        stats["sample_entries"] = sample

        ok_f, n_f, _ = _load_json(self.metadata_fixed_file)
        stats["metadata_fixed_valid"] = ok_f
        stats["metadata_fixed_entries"] = n_f

        if ok and n != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Metadata entries ({n}) != image count ({stats['total_images']})"
            )
        if ok_f and n_f != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Fixed metadata entries ({n_f}) != image count ({stats['total_images']})"
            )

        # Validate conversations structure for sample entries
        if stats["metadata_valid"]:
            for entry in stats["sample_entries"]:
                conv = entry.get("conversations")
                if not isinstance(conv, list) or len(conv) != 2:
                    stats["mismatched_files"].append(
                        f"Entry {entry.get('id','unknown')} conversations malformed"
                    )

        return stats


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert VinciCoder web2html parquets to Qwen Series format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=str,
        required=True,
        help="Base output directory (required). Writes into {output_dir}/qwen_series/{category_name}/",
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
        help="Output category folder under qwen_series (default: vincicoder).",
    )
    p.add_argument(
        "--prefix",
        type=str,
        default="vincicoder",
        help="ID / filename prefix and metadata prefix (default: vincicoder).",
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
    )

    converter = VinciCoderParquetToQwenSeries(cfg)
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
