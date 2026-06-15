#!/usr/bin/env python3
"""
Transform ms_swift JSONL format to code-anything directory structure.

Input format (JSONL, one JSON per line):
  {
    "messages": [
      {"role": "user",      "content": "<image>\\nDrawing from the webpage screenshot, create corresponding HTML and CSS code.\\n"},
      {"role": "assistant", "content": "<!DOCTYPE html>..."}
    ],
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Images are stored inside tar files at <tar-dir>/images-XXXXX.tar.

Output structure:
  {output}/code-anything/3_web/
      images/web_000000.png ...
      codes/web_000000.html ...
      meta_data_web.json

Each metadata entry:
  {
      "source": "htmlSlicer screenshot database",
      "code_language": "html",
      "image_path": "code-anything/3_web/images/web_000000.png",
      "code": "```html\\n<!DOCTYPE html>...\\n```"
  }
"""

import argparse
import json
import logging
import re
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


SUBDIR = "3_web"
PREFIX = "web"
CODE_LANGUAGE = "html"
CODE_EXTENSION = ".html"
FENCE_TAG = "html"
SOURCE_LABEL = "htmlSlicer screenshot database"


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)```\s*$", re.DOTALL)


def _strip_markdown_fences(code: str) -> str:
    """Strip outermost markdown fences (```lang ... ```) for raw code files."""
    m = _FENCE_RE.match(code.strip())
    if m:
        return m.group(1).rstrip("\n")
    return code


def _ensure_fenced(code: str, fence_tag: str) -> str:
    """Ensure code is wrapped in markdown fences for the metadata field."""
    stripped = code.strip()
    if stripped.startswith("```"):
        return stripped
    return f"```{fence_tag}\n{stripped}\n```"


def _extract_assistant_content(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Find the first non-empty assistant message content."""
    for msg in messages or []:
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if content and isinstance(content, str) and content.strip():
                return content.strip()
    return None


def _parse_image_ref(image_ref: str) -> Tuple[str, str]:
    """
    Parse 'images-00000.tar/chunk_0_row_0.png' into (tar_name, member_name).
    Returns ('', image_ref) when the path has no tar prefix.
    """
    parts = image_ref.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", image_ref


class TarPool:
    """Lazily open tar files; index members on first access; cache handles."""

    def __init__(self, tar_dir: Path):
        self.tar_dir = tar_dir
        self._handles: Dict[str, tarfile.TarFile] = {}
        self._indices: Dict[str, Dict[str, tarfile.TarInfo]] = {}

    def _ensure(self, tar_name: str) -> Optional[Tuple[tarfile.TarFile, Dict[str, tarfile.TarInfo]]]:
        if tar_name in self._handles:
            return self._handles[tar_name], self._indices[tar_name]

        tar_path = self.tar_dir / tar_name
        if not tar_path.is_file():
            logger.warning("Tar file missing: %s", tar_path)
            return None

        logger.info("Indexing %s ...", tar_path)
        tf = tarfile.open(tar_path, "r")
        members: Dict[str, tarfile.TarInfo] = {}
        for m in tf.getmembers():
            if m.isfile():
                clean = m.name.lstrip("./")
                members[clean] = m
                # Also index by basename to be lenient with deeper paths.
                base = clean.rsplit("/", 1)[-1]
                members.setdefault(base, m)
        self._handles[tar_name] = tf
        self._indices[tar_name] = members
        logger.info("  %d members indexed in %s", len(members), tar_name)
        return tf, members

    def read(self, tar_name: str, member_name: str) -> Optional[bytes]:
        ensured = self._ensure(tar_name)
        if ensured is None:
            return None
        tf, members = ensured
        m = members.get(member_name) or members.get(member_name.lstrip("./"))
        if m is None:
            return None
        f = tf.extractfile(m)
        if f is None:
            return None
        try:
            return f.read()
        finally:
            f.close()

    def close(self) -> None:
        for tf in self._handles.values():
            try:
                tf.close()
            except Exception:
                pass
        self._handles.clear()
        self._indices.clear()


def _iter_jsonl(
    path: Path, num_samples: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    """Read JSONL into a list (limited by num_samples if > 0). Returns (entries, skipped)."""
    entries: List[Dict[str, Any]] = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping line %d (invalid JSON): %s", i + 1, e)
                skipped += 1
                continue
            if num_samples > 0 and len(entries) >= num_samples:
                break
    return entries, skipped


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL to code-anything format (3_web)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        required=True,
        help="Input ms_swift JSONL file",
    )
    parser.add_argument(
        "--tar-dir",
        type=str,
        required=True,
        help="Directory containing images-XXXXX.tar files",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output base directory (creates code-anything/3_web/ underneath)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Limit number of entries to process (for testing)",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Starting index for output file naming (default: 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process data without writing files",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging"
    )
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = Path(args.input)
    tar_dir = Path(args.tar_dir)
    output_dir = Path(args.output)

    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)
    if not tar_dir.is_dir():
        logger.error("Tar directory not found: %s", tar_dir)
        sys.exit(1)

    web_dir = output_dir / "code-anything" / SUBDIR
    images_dir = web_dir / "images"
    codes_dir = web_dir / "codes"

    if not args.dry_run:
        images_dir.mkdir(parents=True, exist_ok=True)
        codes_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Input JSONL: %s", input_path)
    logger.info("Tar dir:     %s", tar_dir)
    logger.info("Output dir:  %s", output_dir)
    logger.info("Images:      %s", images_dir)
    logger.info("Codes:       %s", codes_dir)
    logger.info("Dry run:     %s", args.dry_run)

    # Read JSONL
    logger.info("Reading JSONL ...")
    entries, json_skipped = _iter_jsonl(input_path, args.limit or 0)
    logger.info("Loaded %d entries (json_skipped=%d)", len(entries), json_skipped)

    pool = TarPool(tar_dir)

    metadata: List[Dict[str, Any]] = []
    exported = 0
    skipped_image = 0
    skipped_code = 0
    skipped_error = 0

    start = time.time()

    try:
        from tqdm import tqdm
        iterator = tqdm(entries, desc="Transforming", unit="rows")
    except ImportError:
        iterator = entries

    try:
        for entry in iterator:
            # Extract code from assistant message
            code = _extract_assistant_content(entry.get("messages", []))
            if not code:
                skipped_code += 1
                continue

            # Extract image ref
            images_list = entry.get("images") or []
            if not images_list or not isinstance(images_list, list):
                skipped_image += 1
                continue
            image_ref = images_list[0]
            if not isinstance(image_ref, str) or not image_ref:
                skipped_image += 1
                continue

            tar_name, member_name = _parse_image_ref(image_ref)
            if not tar_name:
                skipped_image += 1
                continue

            try:
                image_bytes = pool.read(tar_name, member_name)
                if image_bytes is None:
                    skipped_image += 1
                    continue

                output_idx = args.start_index + exported
                file_id = f"{PREFIX}_{output_idx:06d}"

                if not args.dry_run:
                    with open(images_dir / f"{file_id}.png", "wb") as fh:
                        fh.write(image_bytes)

                    raw_code = _strip_markdown_fences(code)
                    with open(codes_dir / f"{file_id}{CODE_EXTENSION}", "w", encoding="utf-8") as fh:
                        fh.write(raw_code)

                fenced_code = _ensure_fenced(code, FENCE_TAG)
                metadata.append(
                    {
                        "source": SOURCE_LABEL,
                        "code_language": CODE_LANGUAGE,
                        "image_path": f"code-anything/{SUBDIR}/images/{file_id}.png",
                        "code": fenced_code,
                    }
                )
                exported += 1

            except Exception as e:
                logger.debug("Error processing entry: %s", e)
                skipped_error += 1
    finally:
        pool.close()

    # Write metadata
    meta_path = web_dir / f"meta_data_{PREFIX}.json"
    if not args.dry_run:
        logger.info("Writing metadata (%d entries) to %s", len(metadata), meta_path)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2, ensure_ascii=False)
    else:
        logger.info("[DRY RUN] Would write %d metadata entries to %s", len(metadata), meta_path)

    elapsed = time.time() - start
    logger.info(
        "Done. exported=%d  skipped_image=%d  skipped_code=%d  "
        "skipped_error=%d  elapsed=%.1fs",
        exported, skipped_image, skipped_code, skipped_error, elapsed,
    )


if __name__ == "__main__":
    main()
