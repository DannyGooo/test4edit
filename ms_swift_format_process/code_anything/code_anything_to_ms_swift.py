#!/usr/bin/env python3
"""
code-anything zip -> MS-Swift JSONL converter.

Reads one of the per-category zips produced by the `code-anything` pipeline
(see ms_swift_format_process/code_anything/gdown.sh) and emits the same
tar-shard + JSONL layout as the baseline ms-swift fetchers
(see ms_swift_format_process/baseline_prepare/web2m_fetcher.py).

Input zip layout (auto-detected; varies per zip):
    <zip_root_prefix>/<category_dir>/
        images/<src_prefix>_NNNNNN.png
        codes/<src_prefix>_NNNNNN.<ext>
        meta_data_<prefix>.json   (a JSON array of entries)

Each metadata entry has the shape:
    {
      "source": "...",
      "code_language": "python|svg|html|latex|...",
      "image_path": "<category_root>/images/<src_prefix>_NNNNNN.png",
      "code": "```<lang>\\n<code>\\n```"  # may also be raw (e.g. 11_Formulation)
    }

Output layout:
    {output_dir}/ms_swift/{category_name}/
        images-00000.tar, images-00001.tar, ...    # 5000 PNGs per shard
        meta_data_{prefix}.jsonl                   # fenced assistant content
        meta_data_{prefix}_raw.jsonl               # fences stripped
        .{prefix}_checkpoint.json                  # crash-safe resume

Each JSONL line:
    {"messages":[{"role":"user","content":"<image>\\nTask: <task>\\nWrite <lang> code ..."},
                 {"role":"assistant","content":"<code>"}],
     "images":["images-00000.tar/<prefix>_NNNNNN.png"]}

The {task} substitution comes from CATEGORY_TO_TASK[category_name] (override
with --task). {language} comes from each entry's code_language field.

Usage:
    python code_anything_to_ms_swift.py \\
        --input-zip /path/to/code_anything/cad.zip \\
        -o /path/to/output

    python code_anything_to_ms_swift.py \\
        --input-zip /path/to/code_anything/svg.zip \\
        -o /path/to/output --resume

    python code_anything_to_ms_swift.py \\
        --input-zip /path/to/code_anything/cad.zip \\
        -o /path/to/output --verify
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import signal
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Reuse the tar shard writer + scan/repair utilities from baseline_prepare.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "baseline_prepare"))
from tar_shard_writer import (  # noqa: E402
    TarShardWriter,
    count_tar_png_members,
    scan_present_indices,
    validate_and_repair_shards,
    verify_shard_sequence,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


DEFAULT_USER_PROMPT = (
    "<image>\nTask: {task}\nWrite {language} code that reproduces this image."
)

# Maps the auto-detected category_name (folder name inside the zip) to a
# human-readable task label substituted into DEFAULT_USER_PROMPT's {task}.
# Unknown categories fall back to category_name itself.
CATEGORY_TO_TASK: Dict[str, str] = {
    "5_svg":                       "SVG generation",
    "4_chart":                     "Chart generation",
    "12_cad":                      "CAD model generation",
    "11_Formulation":              "Math formula typesetting",
    "8_CircuiTikZ":                "Circuit diagram generation",
    "10_ABCNotation":              "Music notation generation",
    "7_Chemical":                  "Chemical structure generation",
    "7_Chemical_Smiles":           "SMILES generation",
    "7_Biological_Structure_100k": "Protein structure visualization",
    "3_Math_Graphics":             "Math graphics generation",
    "2_Diagram_FlowChart":         "Flowchart and diagram generation",
    "1_Table":                     "Table generation",
    "6_Slides_Marp":               "Marp slide generation",
    "6_Slides_Beamer":             "Beamer slide generation",
}

# Strips a single fenced block ```<lang> ... ``` (lang optional).
_FENCE_RE = re.compile(r"^\s*```[^\n`]*\n([\s\S]*?)\n```\s*$")
_META_PREFIX_RE = re.compile(r"meta_data_(.+)\.json$")


def _strip_fence(code: str) -> str:
    """Strip a leading/trailing ```lang ... ``` markdown fence if present.

    Returns the inner code on a match; otherwise returns the input stripped.
    """
    if not code:
        return ""
    m = _FENCE_RE.match(code)
    if m:
        return m.group(1).strip("\n")
    return code.strip()


def _process_image_bytes(raw: bytes) -> Optional[bytes]:
    """Decode + re-encode an image to RGB PNG bytes (lifted from web2m_fetcher)."""
    from PIL import Image

    try:
        pil_img = Image.open(io.BytesIO(raw))
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
    except Exception:
        return None


@dataclass
class ConverterConfig:
    output_dir: Path
    input_zip: Path
    category_name: Optional[str]      # default: auto-detected from zip
    prefix: Optional[str]             # default: auto-detected from meta_data_<prefix>.json
    user_prompt_template: str
    task: Optional[str]               # default: looked up in CATEGORY_TO_TASK
    batch_size: int
    checkpoint_interval: int
    max_samples: Optional[int]
    resume: bool
    scan_existing_tars: bool


class CodeAnythingToMSSwift:
    """Convert a single code-anything zip to ms-swift tar shards + JSONL."""

    def __init__(self, cfg: ConverterConfig):
        self.cfg = cfg
        self._zip: Optional[zipfile.ZipFile] = None

        # These get populated by _open_and_inspect_zip().
        self.category_name: str = ""
        self.prefix: str = ""
        self.zip_root_prefix: str = ""
        self.meta_member: str = ""
        self._metadata: List[Dict[str, Any]] = []

        # Resolved by _resolve_task() after _open_and_inspect_zip().
        self.task: str = ""

        # Populated by _setup_directories() after detection.
        self.base_dir: Path = Path()
        self.tar_writer: Optional[TarShardWriter] = None
        self.checkpoint_file: Path = Path()
        self.metadata_file: Path = Path()
        self.metadata_raw_file: Path = Path()

        self.exported_count = 0
        self.entry_idx = 0
        self.present_indices: Dict[int, str] = {}

        self._metadata_stream = None
        self._metadata_raw_stream = None

        self.stats: Dict[str, Any] = {
            "total_processed": 0,
            "total_exported": 0,
            "skipped_missing_image": 0,
            "skipped_image_decode_error": 0,
            "skipped_missing_code": 0,
            "skipped_errors": 0,
            "reused_existing": 0,
            "start_time": None,
        }

        self._original_sigint = None
        self._original_sigterm = None

    # ------------------------------------------------------------------
    # Zip introspection
    # ------------------------------------------------------------------

    def _open_and_inspect_zip(self) -> None:
        """Open the zip, find the meta file, derive prefix/category/root_prefix."""
        self._zip = zipfile.ZipFile(self.cfg.input_zip, "r")

        # Find the (typically single) meta_data_<prefix>.json member.
        meta_candidates = [
            n for n in self._zip.namelist()
            if n.endswith(".json") and Path(n).name.startswith("meta_data_")
        ]
        if not meta_candidates:
            raise RuntimeError(
                f"No meta_data_*.json member found in {self.cfg.input_zip}"
            )
        if len(meta_candidates) > 1:
            logger.warning(
                "Multiple meta_data_*.json members found; using first: %s",
                meta_candidates[0],
            )
        self.meta_member = meta_candidates[0]
        meta_filename = Path(self.meta_member).name

        # Derive prefix from meta_data_<prefix>.json.
        m = _META_PREFIX_RE.search(meta_filename)
        inferred_prefix = m.group(1) if m else "data"
        self.prefix = self.cfg.prefix or inferred_prefix

        # Derive category_name from the directory the meta file sits in.
        meta_dir = str(Path(self.meta_member).parent).replace("\\", "/")
        inferred_category = Path(meta_dir).name or self.cfg.input_zip.stem
        self.category_name = self.cfg.category_name or inferred_category

        logger.info("Zip: %s", self.cfg.input_zip)
        logger.info("  meta_member       = %s", self.meta_member)
        logger.info("  inferred prefix   = %s (using: %s)", inferred_prefix, self.prefix)
        logger.info("  inferred category = %s (using: %s)", inferred_category, self.category_name)

        # Load the metadata array. The cad.zip metadata is ~50 MB; svg.zip ~700 MB;
        # well within available RAM. Streaming-parse is not worth the complexity.
        with self._zip.open(self.meta_member) as f:
            self._metadata = json.load(f)
        if not isinstance(self._metadata, list):
            raise RuntimeError(
                f"Expected JSON array in {self.meta_member}, got {type(self._metadata).__name__}"
            )
        logger.info("  metadata entries  = %d", len(self._metadata))

        # Auto-detect the zip's root prefix by matching the first entry's
        # image_path against a real zip member.
        first_image_path = (self._metadata[0] or {}).get("image_path", "") if self._metadata else ""
        if not first_image_path:
            raise RuntimeError("First metadata entry has no 'image_path'; cannot detect root prefix")
        self.zip_root_prefix = self._detect_zip_root_prefix(first_image_path)
        logger.info("  zip_root_prefix   = %r", self.zip_root_prefix)

    def _detect_zip_root_prefix(self, image_path: str) -> str:
        """Find the leading path prefix the zip prepends to relative image_paths.

        E.g. cad.zip has zip members rooted at
        'data3/.../htmlSlicer/output/code-anything/12_cad/...' but the metadata
        records 'code-anything/12_cad/...'. The prefix here is
        'data3/.../htmlSlicer/output/'. For svg.zip the prefix is ''.
        """
        names = set(self._zip.namelist())
        if image_path in names:
            return ""
        # Try every suffix-matching member.
        for n in names:
            if n.endswith(image_path):
                return n[: -len(image_path)]
        raise RuntimeError(
            f"Cannot locate image_path={image_path!r} in zip {self.cfg.input_zip}; "
            "zip layout does not match expected code-anything format."
        )

    def _resolve_image_member(self, image_path: str) -> str:
        return self.zip_root_prefix + image_path

    def _resolve_task(self) -> None:
        """Resolve the {task} substitution value once per run.

        Precedence: --task CLI override > CATEGORY_TO_TASK[category_name] >
        category_name fallback.
        """
        if self.cfg.task:
            self.task = self.cfg.task
        else:
            self.task = CATEGORY_TO_TASK.get(self.category_name, self.category_name)

    # ------------------------------------------------------------------
    # Directory / stream setup
    # ------------------------------------------------------------------

    def _setup_directories(self) -> None:
        self.base_dir = self.cfg.output_dir / "ms_swift" / self.category_name
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.tar_writer = TarShardWriter(self.base_dir)
        self.checkpoint_file = self.base_dir / f".{self.prefix}_checkpoint.json"
        self.metadata_file = self.base_dir / f"meta_data_{self.prefix}.jsonl"
        self.metadata_raw_file = self.base_dir / f"meta_data_{self.prefix}_raw.jsonl"

    def _init_streams(self, append: bool) -> None:
        for metadata_file, attr in [
            (self.metadata_file, "_metadata_stream"),
            (self.metadata_raw_file, "_metadata_raw_stream"),
        ]:
            mode = "a" if (append and metadata_file.exists()) else "w"
            setattr(self, attr, open(metadata_file, mode, encoding="utf-8"))

    def _append_entries(self, fenced: Dict[str, Any], raw: Dict[str, Any]) -> None:
        if self._metadata_stream:
            self._metadata_stream.write(json.dumps(fenced, ensure_ascii=False) + "\n")
        if self._metadata_raw_stream:
            self._metadata_raw_stream.write(json.dumps(raw, ensure_ascii=False) + "\n")

    def _flush_streams(self) -> None:
        if self._metadata_stream:
            self._metadata_stream.flush()
        if self._metadata_raw_stream:
            self._metadata_raw_stream.flush()

    def _close_streams(self) -> None:
        for attr in ["_metadata_stream", "_metadata_raw_stream"]:
            s = getattr(self, attr, None)
            if s:
                s.close()
                setattr(self, attr, None)

    # ------------------------------------------------------------------
    # Signal handlers + checkpoint
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        def handler(signum, _frame):
            logger.info("Received signal %s; saving checkpoint...", signum)
            if self.tar_writer is not None:
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
            self.entry_idx = int(checkpoint.get("entry_idx", 0))
            self.stats = checkpoint.get("stats", self.stats)
            logger.info(
                "Resumed checkpoint: exported=%d entry_idx=%d",
                self.exported_count,
                self.entry_idx,
            )
            return True
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)
            return False

    def _save_checkpoint(self) -> None:
        payload = {
            "exported_count": self.exported_count,
            "entry_idx": self.entry_idx,
            "category_name": self.category_name,
            "prefix": self.prefix,
            "stats": self.stats,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file.write_text(json.dumps(payload, indent=2))

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify_output(self) -> Dict[str, Any]:
        # Open and inspect zip (sets base_dir paths via category_name).
        if self._zip is None:
            self._open_and_inspect_zip()
        self._resolve_task()
        self._setup_directories()

        stats: Dict[str, Any] = {
            "category_name": self.category_name,
            "prefix": self.prefix,
            "total_images": 0,
            "metadata_entries": 0,
            "metadata_raw_entries": 0,
            "metadata_valid": False,
            "metadata_raw_valid": False,
            "mismatched_files": [],
            "sample_entries": [],
        }

        if self.base_dir.exists():
            stats["total_images"] = count_tar_png_members(self.base_dir)

        ok, n, sample = _count_jsonl(self.metadata_file, sample_n=3)
        stats["metadata_valid"] = ok
        stats["metadata_entries"] = n if ok else -1
        stats["sample_entries"] = sample

        ok_r, n_r, _ = _count_jsonl(self.metadata_raw_file, sample_n=0)
        stats["metadata_raw_valid"] = ok_r
        stats["metadata_raw_entries"] = n_r if ok_r else -1

        if ok and n != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Metadata entries ({n}) != image count ({stats['total_images']})"
            )
        if ok_r and n_r != stats["total_images"]:
            stats["mismatched_files"].append(
                f"Raw metadata entries ({n_r}) != image count ({stats['total_images']})"
            )

        return stats

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _format_file_id(self, num: int) -> str:
        return f"{self.prefix}_{num:06d}"

    def _process_entry(self, entry: Dict[str, Any]) -> bool:
        self.stats["total_processed"] += 1

        image_path = entry.get("image_path")
        code = entry.get("code")
        language = entry.get("code_language") or "code"

        if not image_path:
            self.stats["skipped_missing_image"] += 1
            return False

        raw_code = _strip_fence(str(code or ""))
        if not raw_code.strip():
            self.stats["skipped_missing_code"] += 1
            return False

        member = self._resolve_image_member(image_path)
        try:
            img_raw = self._zip.read(member)
        except KeyError:
            self.stats["skipped_missing_image"] += 1
            return False

        existing_path = (
            self.present_indices.get(self.exported_count)
            if self.cfg.scan_existing_tars
            else None
        )
        if existing_path is not None:
            image_ref = [existing_path]
            self.stats["reused_existing"] += 1
        else:
            img_bytes = _process_image_bytes(img_raw)
            if img_bytes is None:
                self.stats["skipped_image_decode_error"] += 1
                return False
            file_id = self._format_file_id(self.exported_count)
            image_ref = [self.tar_writer.add_image(file_id, img_bytes, self.exported_count)]

        user_content = self.cfg.user_prompt_template.format(
            language=language, task=self.task
        )
        fenced_assistant = f"```{language}\n{raw_code}\n```"

        entry_fenced = {
            "messages": [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": fenced_assistant},
            ],
            "images": image_ref,
        }
        entry_raw = {
            "messages": [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": raw_code},
            ],
            "images": image_ref,
        }
        self._append_entries(entry_fenced, entry_raw)

        self.exported_count += 1
        self.stats["total_exported"] += 1
        return True

    def run(self) -> Dict[str, Any]:
        self.stats["start_time"] = time.time()
        self._setup_signal_handlers()
        self._open_and_inspect_zip()
        self._resolve_task()
        self._setup_directories()

        if self.cfg.scan_existing_tars:
            logger.info("Validating + repairing existing tar shards...")
            validate_and_repair_shards(self.base_dir, self.prefix)
            logger.info("Scanning existing tar shards for already-exported samples...")
            self.present_indices = scan_present_indices(self.base_dir, self.prefix)
            if self.present_indices:
                idxs = sorted(self.present_indices.keys())
                logger.info(
                    "Found %d existing PNGs (min=%d, max=%d)",
                    len(idxs),
                    idxs[0],
                    idxs[-1],
                )
            self.exported_count = 0
            self.entry_idx = 0
            is_resuming = False
        else:
            is_resuming = self.cfg.resume and self._load_checkpoint()

        self._init_streams(append=is_resuming)

        logger.info("code-anything -> ms-swift")
        logger.info("  output            = %s", self.base_dir)
        logger.info("  task              = %r", self.task)
        logger.info("  max_samples       = %s", self.cfg.max_samples or "all")
        logger.info("  resume            = %s", is_resuming)
        logger.info("  scan_existing     = %s", self.cfg.scan_existing_tars)

        completed_successfully = False
        try:
            try:
                from tqdm import tqdm
            except ImportError:
                tqdm = None

            iterable: Iterable[int] = range(self.entry_idx, len(self._metadata))
            pbar = (
                tqdm(iterable, total=len(self._metadata), initial=self.entry_idx,
                     desc=f"{self.category_name}", unit="entry")
                if tqdm is not None
                else None
            )

            for i in iterable:
                if self.cfg.max_samples is not None and self.exported_count >= self.cfg.max_samples:
                    logger.info("Reached export limit: %d", self.cfg.max_samples)
                    break

                entry = self._metadata[i]
                try:
                    self._process_entry(entry)
                except Exception as e:
                    self.stats["skipped_errors"] += 1
                    logger.debug("entry %d: %r", i, e)

                self.entry_idx = i + 1

                if pbar is not None:
                    pbar.update(1)
                    pbar.set_postfix(
                        exported=self.exported_count,
                        noimg=self.stats["skipped_missing_image"],
                        nocode=self.stats["skipped_missing_code"],
                        err=self.stats["skipped_errors"],
                    )

                if self.entry_idx % self.cfg.checkpoint_interval == 0:
                    if self.tar_writer is not None:
                        self.tar_writer.flush()
                    self._flush_streams()
                    self._save_checkpoint()

            if pbar is not None:
                pbar.close()

            completed_successfully = True
        finally:
            if self.tar_writer is not None:
                self.tar_writer.close()
            self._close_streams()
            self._save_checkpoint()
            if (
                completed_successfully
                and self.cfg.max_samples is None
                and self.entry_idx >= len(self._metadata)
                and self.checkpoint_file.exists()
            ):
                try:
                    self.checkpoint_file.unlink()
                except Exception:
                    pass
            self._restore_signal_handlers()
            if self._zip is not None:
                self._zip.close()
                self._zip = None

            issues = verify_shard_sequence(self.base_dir, self.prefix)
            if issues:
                logger.warning("Shard sequence check found issues:")
                for msg in issues:
                    logger.warning("  - %s", msg)
            else:
                logger.info("Shard sequence check passed.")

        elapsed = time.time() - (self.stats["start_time"] or time.time())
        rate = self.stats["total_exported"] / elapsed if elapsed > 0 else 0.0
        logger.info(
            "Done. processed=%d exported=%d skipped=%d rate=%.2f/s",
            self.stats["total_processed"],
            self.stats["total_exported"],
            self.stats["total_processed"] - self.stats["total_exported"],
            rate,
        )
        return self.stats


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
        description="Convert a code-anything zip to MS-Swift JSONL format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input-zip", type=str, required=True,
                   help="Path to one of the code-anything per-category zips.")
    p.add_argument("-o", "--output-dir", type=str, required=True,
                   help="Base output directory. Writes into {output_dir}/ms_swift/{category_name}/.")
    p.add_argument("--category-name", type=str, default=None,
                   help="Override output category subdir (default: auto-detected from zip).")
    p.add_argument("--prefix", type=str, default=None,
                   help="Override image/JSONL filename prefix (default: from meta_data_<prefix>.json).")
    p.add_argument("--user-prompt", type=str, default=DEFAULT_USER_PROMPT,
                   help="User prompt template; '{language}' is substituted with entry['code_language'] and "
                        "'{task}' with the resolved task label. "
                        f"Default: {DEFAULT_USER_PROMPT!r}")
    p.add_argument("--task", type=str, default=None,
                   help="Override task label substituted into the {task} placeholder "
                        "(default: derived from category_name via CATEGORY_TO_TASK, "
                        "with category_name as fallback).")
    p.add_argument("--batch-size", type=int, default=128, help="(reserved for future use)")
    p.add_argument("--checkpoint-interval", type=int, default=2000,
                   help="Save checkpoint every N entries (default: 2000).")
    p.add_argument("-n", "--num-samples", type=int, default=None,
                   help="Maximum number of samples to export (default: all).")
    p.add_argument("--resume", action="store_true", help="Resume from checkpoint if it exists.")
    p.add_argument("--no-resume", action="store_true", help="Ignore checkpoint and start fresh.")
    p.add_argument("--scan-tars", action="store_true",
                   help="Scan existing tar shards and only fetch samples whose "
                        "{prefix}_NNNNNN.png is missing; rebuilds JSONL to match.")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    p.add_argument("--verify", action="store_true", help="Verify output files and exit.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = ConverterConfig(
        output_dir=Path(args.output_dir),
        input_zip=Path(args.input_zip),
        category_name=args.category_name,
        prefix=args.prefix,
        user_prompt_template=args.user_prompt,
        task=args.task,
        batch_size=args.batch_size,
        checkpoint_interval=args.checkpoint_interval,
        max_samples=args.num_samples,
        resume=(args.resume or (not args.no_resume)),
        scan_existing_tars=args.scan_tars,
    )

    converter = CodeAnythingToMSSwift(cfg)
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
