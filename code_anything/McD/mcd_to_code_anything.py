#!/usr/bin/env python3
"""
MCD to Code-Anything Converter.

Reads MCD (MultimodalCodingDataset) JSON + images ZIP and outputs
html and chart categories in code-anything format.

Output structure:
    {output}/code-anything/
        3_web/
            images/web_000000.png ...
            codes/web_000000.html ...
            meta_data_web.json
        4_chart/
            images/chart_000000.png ...
            codes/chart_000000.py ...
            meta_data_chart.json

Usage:
    python mcd_to_code_anything.py \
        --json-data /path/to/mcd_598k.json \
        --images-zip /path/to/mcd_images.zip \
        -o /path/to/output \
        --category both

    python mcd_to_code_anything.py \
        --json-data /path/to/mcd_598k.json \
        --images-zip /path/to/mcd_images.zip \
        -o /path/to/output \
        --limit 5 --category both -v
"""

import argparse
import io
import json
import logging
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class CategoryConfig:
    """Per-category settings for code-anything output."""

    category_key: str       # MCD category value (e.g. "html", "chart")
    subdir: str             # Output subdirectory (e.g. "3_web")
    prefix: str             # File name prefix (e.g. "web")
    code_language: str      # Language label (e.g. "html", "python")
    code_extension: str     # File extension for code files (e.g. ".html", ".py")
    fence_tag: str          # Markdown fence language tag (e.g. "html", "python")
    source_label: str       # Source field in metadata


CATEGORY_CONFIGS = {
    "html": CategoryConfig(
        category_key="html",
        subdir="3_web",
        prefix="web",
        code_language="html",
        code_extension=".html",
        fence_tag="html",
        source_label="MCD html screenshot database",
    ),
    "chart": CategoryConfig(
        category_key="chart",
        subdir="4_chart",
        prefix="chart",
        code_language="python",
        code_extension=".py",
        fence_tag="python",
        source_label="MCD chart screenshot database",
    ),
}


class MCDToCodeAnything:
    """Converts MCD dataset entries to code-anything directory format."""

    def __init__(
        self,
        json_data_path: str,
        images_zip_path: str,
        output_dir: str,
        categories: List[str],
        limit: Optional[int] = None,
        verbose: bool = False,
    ):
        self.json_data_path = Path(json_data_path)
        self.images_zip_path = Path(images_zip_path)
        self.output_dir = Path(output_dir)
        self.categories = categories
        self.limit = limit
        self.zip_file: Optional[zipfile.ZipFile] = None

        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # Image loading (pattern from mcd_fetcher.py:261-294)
    # ------------------------------------------------------------------

    def _load_image_from_zip(self, image_path: str) -> Optional[bytes]:
        """Load image from ZIP file and convert to RGB PNG bytes."""
        from PIL import Image

        try:
            image_data = self.zip_file.read(image_path)
            pil_img = Image.open(io.BytesIO(image_data))

            # Normalize to RGB
            if pil_img.mode in ("RGBA", "LA", "P"):
                if pil_img.mode == "P":
                    pil_img = pil_img.convert("RGBA")
                background = Image.new("RGB", pil_img.size, (255, 255, 255))
                if pil_img.mode == "RGBA":
                    background.paste(pil_img, mask=pil_img.split()[3])
                else:
                    background.paste(pil_img, mask=pil_img.split()[1])
                pil_img = background
            elif pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue()

        except KeyError:
            return None
        except Exception as e:
            logger.debug(f"Image loading error for {image_path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Code extraction helpers
    # ------------------------------------------------------------------

    def _extract_code_from_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Find the assistant response text in a messages list."""
        if not messages:
            return None
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if content and isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    _FENCE_RE = re.compile(
        r"^```[a-zA-Z]*\s*\n(.*?)```\s*$", re.DOTALL
    )

    def _strip_markdown_fences(self, code: str) -> str:
        """Strip outermost markdown fences (```lang ... ```) for raw code files."""
        m = self._FENCE_RE.match(code.strip())
        if m:
            return m.group(1).rstrip("\n")
        return code

    # ------------------------------------------------------------------
    # ZIP path normalization (pattern from mcd_fetcher.py:468-473)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_zip_path(image_path_str: str) -> str:
        """Ensure path has 'mcd_images/' prefix for ZIP lookup."""
        if image_path_str.startswith("mcd_images/"):
            return image_path_str
        return f"mcd_images/{image_path_str}"

    # ------------------------------------------------------------------
    # Per-category processing
    # ------------------------------------------------------------------

    def _process_category(
        self,
        samples: List[Dict[str, Any]],
        cfg: CategoryConfig,
    ) -> List[Dict[str, Any]]:
        """
        Process all samples for one category.

        Returns the metadata list for this category.
        """
        from tqdm import tqdm

        # Setup output dirs
        cat_dir = self.output_dir / "code-anything" / cfg.subdir
        images_dir = cat_dir / "images"
        codes_dir = cat_dir / "codes"
        images_dir.mkdir(parents=True, exist_ok=True)
        codes_dir.mkdir(parents=True, exist_ok=True)

        metadata: List[Dict[str, Any]] = []
        exported = 0
        skipped_image = 0
        skipped_code = 0
        skipped_error = 0

        effective = samples[: self.limit] if self.limit else samples

        pbar = tqdm(effective, desc=f"Processing {cfg.category_key}", unit="samples")
        for sample in pbar:
            # Extract image path
            images_list = sample.get("images")
            if not images_list or not isinstance(images_list, list) or len(images_list) == 0:
                skipped_image += 1
                continue
            image_path_str = images_list[0]
            if not isinstance(image_path_str, str):
                skipped_image += 1
                continue

            # Extract code from messages
            messages = sample.get("messages", [])
            code = self._extract_code_from_messages(messages)
            if not code:
                skipped_code += 1
                continue

            try:
                # Load image from ZIP
                zip_path = self._normalize_zip_path(image_path_str)
                image_bytes = self._load_image_from_zip(zip_path)
                if image_bytes is None:
                    skipped_image += 1
                    continue

                # File naming
                file_id = f"{cfg.prefix}_{exported:06d}"

                # Save image
                image_out = images_dir / f"{file_id}.png"
                with open(image_out, "wb") as f:
                    f.write(image_bytes)

                # Save raw code file (fences stripped)
                raw_code = self._strip_markdown_fences(code)
                code_out = codes_dir / f"{file_id}{cfg.code_extension}"
                with open(code_out, "w", encoding="utf-8") as f:
                    f.write(raw_code)

                # Build metadata entry (fences kept / ensured)
                fenced_code = self._ensure_fenced(code, cfg.fence_tag)
                metadata_image_path = (
                    f"code-anything/{cfg.subdir}/images/{file_id}.png"
                )
                metadata.append(
                    {
                        "source": cfg.source_label,
                        "code_language": cfg.code_language,
                        "image_path": metadata_image_path,
                        "code": fenced_code,
                    }
                )

                exported += 1
                pbar.set_postfix(exported=exported)

            except Exception as e:
                logger.debug(f"Error processing sample: {e}")
                skipped_error += 1

        pbar.close()

        logger.info(
            f"[{cfg.category_key}] exported={exported}  "
            f"skipped_image={skipped_image}  skipped_code={skipped_code}  "
            f"skipped_error={skipped_error}"
        )

        return metadata

    @staticmethod
    def _ensure_fenced(code: str, fence_tag: str) -> str:
        """Ensure code is wrapped in markdown fences for the metadata field."""
        stripped = code.strip()
        if stripped.startswith("```"):
            return stripped
        return f"```{fence_tag}\n{stripped}\n```"

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Load JSON + ZIP, process each requested category, write metadata."""
        start = time.time()

        # Load JSON data
        logger.info(f"Loading JSON data from {self.json_data_path} ...")
        with open(self.json_data_path, "r", encoding="utf-8") as f:
            all_data: List[Dict[str, Any]] = json.load(f)
        logger.info(f"Loaded {len(all_data):,} total entries")

        # Open ZIP
        logger.info(f"Opening images ZIP: {self.images_zip_path}")
        self.zip_file = zipfile.ZipFile(self.images_zip_path, "r")

        try:
            for cat_key in self.categories:
                cfg = CATEGORY_CONFIGS[cat_key]
                logger.info(f"Filtering for category '{cfg.category_key}' ...")

                cat_samples = [
                    s
                    for s in all_data
                    if (s.get("category") or "").lower() == cfg.category_key
                ]
                logger.info(f"Found {len(cat_samples):,} samples for '{cfg.category_key}'")

                if not cat_samples:
                    logger.warning(f"No samples for category '{cfg.category_key}', skipping.")
                    continue

                metadata = self._process_category(cat_samples, cfg)

                # Write metadata JSON
                cat_dir = self.output_dir / "code-anything" / cfg.subdir
                meta_path = cat_dir / f"meta_data_{cfg.prefix}.json"
                logger.info(f"Writing metadata ({len(metadata):,} entries) to {meta_path}")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

        finally:
            self.zip_file.close()
            self.zip_file = None

        elapsed = time.time() - start
        logger.info(f"Done in {elapsed:.1f}s")


# ======================================================================
# CLI
# ======================================================================


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert MCD dataset to code-anything format (html + chart)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json-data",
        type=str,
        required=True,
        help="Path to MCD JSON file (e.g. mcd_598k.json)",
    )
    parser.add_argument(
        "--images-zip",
        type=str,
        required=True,
        help="Path to mcd_images.zip",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        required=True,
        help="Output base directory",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max samples per category (for testing)",
    )
    parser.add_argument(
        "--category",
        type=str,
        choices=["html", "chart", "both"],
        default="both",
        help="Which category(ies) to process (default: both)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    if args.category == "both":
        categories = ["html", "chart"]
    else:
        categories = [args.category]

    converter = MCDToCodeAnything(
        json_data_path=args.json_data,
        images_zip_path=args.images_zip,
        output_dir=args.output,
        categories=categories,
        limit=args.limit,
        verbose=args.verbose,
    )

    try:
        converter.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
