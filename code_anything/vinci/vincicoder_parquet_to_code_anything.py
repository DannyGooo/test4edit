#!/usr/bin/env python3
"""
VinciCoder img2svg parquet -> Code-Anything format converter.

Input parquet schema (expected):
  - user_content: string
  - assistant_content: string (SVG code, often markdown-fenced)
  - image: binary (PNG bytes)

Output: a ZIP archive at {output_dir}/svg.zip containing:
  code-anything/5_svg/
      images/svg_000000.png, svg_000001.png, ...
      codes/svg_000000.svg, svg_000001.svg, ...
      meta_data_svg.json

Usage:
  python vincicoder_parquet_to_code_anything.py \
    --input /path/to/img2svg_1.parquet \
    -o /path/to/output

  # Test with small subset
  python vincicoder_parquet_to_code_anything.py \
    --input /path/to/img2svg_1.parquet \
    -o /path/to/output \
    --limit 5 -v

  # Dry run (no files written)
  python vincicoder_parquet_to_code_anything.py \
    --input /path/to/img2svg_1.parquet \
    -o /path/to/output \
    --dry-run -v
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (reused patterns from existing converters)
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"```(?:svg|xml|html)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _as_bytes(v: Any) -> Optional[bytes]:
    """Convert parquet image column values to bytes."""
    if v is None:
        return None
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    if isinstance(v, memoryview):
        return v.tobytes()
    return None


def _extract_code_from_markdown(text: str) -> str:
    """
    Strip markdown fences from assistant_content.
    Returns the raw code inside the first fenced block, or the original
    stripped text if no fences are found.
    """
    text = (text or "").strip()
    if not text:
        return ""
    m = _CODE_FENCE_RE.search(text)
    if m:
        return (m.group(1) or "").strip()
    return text


def _ensure_fenced(code: str, fence_tag: str) -> str:
    """Ensure code is wrapped in markdown fences for the metadata field."""
    stripped = code.strip()
    if stripped.startswith("```"):
        return stripped
    return f"```{fence_tag}\n{stripped}\n```"


# ---------------------------------------------------------------------------
# Batch parquet reader
# ---------------------------------------------------------------------------


def _read_parquet_batches(
    path: Path, batch_size: int = 128
) -> Iterable[Tuple[List[Any], List[Any], List[Any]]]:
    """Yield batches as python lists: (user_content, assistant_content, image_bytes)."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    for record_batch in pf.iter_batches(
        batch_size=batch_size,
        columns=["user_content", "assistant_content", "image"],
    ):
        cols = record_batch.to_pydict()
        yield (
            cols.get("user_content", []),
            cols.get("assistant_content", []),
            cols.get("image", []),
        )


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

# SVG category config (mirrors CategoryConfig from mcd_to_code_anything.py)
SVG_SUBDIR = "5_svg"
SVG_PREFIX = "svg"
SVG_CODE_LANGUAGE = "svg"
SVG_CODE_EXTENSION = ".svg"
SVG_FENCE_TAG = "svg"
SVG_SOURCE_LABEL = "VinciCoder img2svg screenshot database"


class VinciCoderSVGToCodeAnything:
    """Converts VinciCoder img2svg parquet to a ZIP archive in code-anything format."""

    def __init__(
        self,
        input_parquet: Path,
        output_dir: Path,
        batch_size: int = 128,
        limit: Optional[int] = None,
        verbose: bool = False,
        dry_run: bool = False,
    ):
        self.input_parquet = input_parquet
        self.output_dir = output_dir
        self.batch_size = batch_size
        self.limit = limit
        self.dry_run = dry_run

        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)

        # Output path: single zip file
        self.zip_path = output_dir / "svg.zip"

        # Stats
        self.stats = {
            "total_processed": 0,
            "total_exported": 0,
            "skipped_missing_image": 0,
            "skipped_missing_code": 0,
            "skipped_errors": 0,
        }

    def run(self) -> Dict[str, Any]:
        from tqdm import tqdm

        start = time.time()

        logger.info("VinciCoder img2svg parquet -> code-anything (ZIP)")
        logger.info("  input:  %s", self.input_parquet)
        logger.info("  output: %s", self.zip_path)
        logger.info("  limit:  %s", self.limit or "all")
        logger.info("  dry_run: %s", self.dry_run)

        metadata: List[Dict[str, Any]] = []
        exported = 0

        # Ensure output directory exists
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        # Open zip file (or use a no-op context in dry-run mode)
        zf: Optional[zipfile.ZipFile] = None
        try:
            if not self.dry_run:
                zf = zipfile.ZipFile(self.zip_path, "w")

            pbar = tqdm(desc="Processing SVG samples", unit="rows")
            try:
                for users, assistants, images in _read_parquet_batches(
                    self.input_parquet, self.batch_size
                ):
                    for user_content, assistant_content, image_val in zip(
                        users, assistants, images
                    ):
                        self.stats["total_processed"] += 1
                        pbar.update(1)

                        # Check export limit
                        if self.limit is not None and exported >= self.limit:
                            raise StopIteration

                        # Extract image bytes
                        img_bytes = _as_bytes(image_val)
                        if not img_bytes:
                            self.stats["skipped_missing_image"] += 1
                            continue

                        # Extract SVG code
                        raw_code = _extract_code_from_markdown(
                            str(assistant_content or "")
                        )
                        if not raw_code.strip():
                            self.stats["skipped_missing_code"] += 1
                            continue

                        try:
                            file_id = f"{SVG_PREFIX}_{exported:06d}"

                            if zf is not None:
                                # Write image (PNG already compressed — use ZIP_STORED)
                                image_arcname = f"code-anything/{SVG_SUBDIR}/images/{file_id}.png"
                                zf.writestr(
                                    zipfile.ZipInfo(image_arcname),
                                    img_bytes,
                                    compress_type=zipfile.ZIP_STORED,
                                )

                                # Write raw SVG code (text — use ZIP_DEFLATED)
                                code_arcname = f"code-anything/{SVG_SUBDIR}/codes/{file_id}{SVG_CODE_EXTENSION}"
                                zf.writestr(
                                    zipfile.ZipInfo(code_arcname),
                                    raw_code.encode("utf-8"),
                                    compress_type=zipfile.ZIP_DEFLATED,
                                )

                            # Build metadata entry (fences ensured)
                            fenced_code = _ensure_fenced(raw_code, SVG_FENCE_TAG)
                            metadata_image_path = (
                                f"code-anything/{SVG_SUBDIR}/images/{file_id}.png"
                            )
                            metadata.append(
                                {
                                    "source": SVG_SOURCE_LABEL,
                                    "code_language": SVG_CODE_LANGUAGE,
                                    "image_path": metadata_image_path,
                                    "code": fenced_code,
                                }
                            )

                            exported += 1
                            self.stats["total_exported"] += 1
                            pbar.set_postfix(exported=exported)

                        except Exception as e:
                            logger.debug("Error processing row: %s", e)
                            self.stats["skipped_errors"] += 1

            except StopIteration:
                logger.info("Reached export limit: %s", self.limit)

            pbar.close()

            # Write metadata JSON into zip (text — use ZIP_DEFLATED)
            if zf is not None:
                meta_arcname = f"code-anything/{SVG_SUBDIR}/meta_data_{SVG_PREFIX}.json"
                meta_json = json.dumps(metadata, indent=2, ensure_ascii=False)
                logger.info(
                    "Writing metadata (%d entries) to %s in zip",
                    len(metadata),
                    meta_arcname,
                )
                zf.writestr(
                    zipfile.ZipInfo(meta_arcname),
                    meta_json.encode("utf-8"),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
            else:
                logger.info("[DRY RUN] Would write %d metadata entries", len(metadata))

        finally:
            if zf is not None:
                zf.close()

        elapsed = time.time() - start
        rate = exported / elapsed if elapsed > 0 else 0.0
        logger.info(
            "Done. processed=%d  exported=%d  skipped=%d  rate=%.2f/s  elapsed=%.1fs",
            self.stats["total_processed"],
            exported,
            self.stats["total_processed"] - exported,
            rate,
            elapsed,
        )
        if not self.dry_run:
            zip_size_mb = self.zip_path.stat().st_size / (1024 * 1024)
            logger.info("  zip size: %.1f MB", zip_size_mb)
        return self.stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert VinciCoder img2svg parquet to code-anything ZIP archive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to img2svg parquet file",
    )
    p.add_argument(
        "-o",
        "--output",
        type=str,
        required=True,
        help="Output directory (writes svg.zip into this directory)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Parquet batch size (default: 128)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max samples to export (for testing)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Process data without writing files",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    converter = VinciCoderSVGToCodeAnything(
        input_parquet=Path(args.input),
        output_dir=Path(args.output),
        batch_size=args.batch_size,
        limit=args.limit,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )

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
