#!/usr/bin/env python3
"""
WebCode2M Dataset Fetcher for MS-Swift format.

Fetches the xcodemind/webcode2m dataset from HuggingFace and exports
to MS-Swift JSONL format for training vision-language models.

Output structure:
    output/ms_swift/web100k/
        images-00000.tar, images-00001.tar, ...
        meta_data_web_100k.jsonl   # For 100k subset
        meta_data_web.jsonl        # Full dataset metadata

Each JSONL line has the shape:
    {"messages": [{"role": "user", "content": "<image>\\n..."},
                  {"role": "assistant", "content": "<html>..."}],
     "images": ["images-00000.tar/web_000000.png"]}

Usage:
    python web2m_fetcher.py -o ./output
    python web2m_fetcher.py -o ./output -n 100000
    python web2m_fetcher.py -o ./output --splits train --resume
    python web2m_fetcher.py -o ./output --verify

    python ms_swift_format_process/baseline_prepare/web2m_fetcher.py -o /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2M/ms-swift_series --resume

    export HF_TOKEN=xxx &&  python ms_swift_format_process/baseline_prepare/web2m_fetcher.py     -o /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2M/ms-swift_series     --resume 2>&1 | tee /tmp/web2m_resume.log  
"""

import argparse
import gc
import io
import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from tar_shard_writer import (
    TarShardWriter,
    count_tar_png_members,
    scan_present_indices,
    validate_and_repair_shards,
    verify_shard_sequence,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WebCode2MFetcher:
    """Fetches WebCode2M dataset and exports to MS-Swift JSONL format."""

    DATASET_ID = "xcodemind/webcode2m"
    PREFIX = "web"
    CATEGORY_NUM = "webcode2m"

    HUMAN_PROMPT = """<image>
Drawing from the webpage screenshot, create corresponding HTML and CSS code.
"""

    def __init__(
        self,
        output_dir: str,
        splits: Optional[List[str]] = None,
        batch_size: int = 100,
        checkpoint_interval: int = 1000,
        memory_limit_gb: float = 50.0,
        hf_token: Optional[str] = None,
        streaming: bool = True,
        lang_filter: Optional[str] = None,
        scan_existing_tars: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.splits = splits or ['train']
        self.batch_size = batch_size
        self.checkpoint_interval = checkpoint_interval
        self.memory_limit_gb = memory_limit_gb
        self.hf_token = hf_token
        self.streaming = streaming
        self.lang_filter = lang_filter
        self.scan_existing_tars = scan_existing_tars
        self.present_indices: Dict[int, str] = {}

        self.base_dir = self.output_dir / 'ms_swift' / self.CATEGORY_NUM
        self.tar_writer = TarShardWriter(self.base_dir)

        self.checkpoint_file = self.base_dir / '.webcode2m_checkpoint.json'
        self.metadata_file = self.base_dir / f'meta_data_{self.PREFIX}.jsonl'
        self.metadata_100k_file = self.base_dir / f'meta_data_{self.PREFIX}_100k.jsonl'

        self.exported_count = 0
        self.current_split_index = 0
        self.current_sample_index = 0
        self.metadata_stream = None

        self.stats = {
            'total_processed': 0,
            'total_exported': 0,
            'skipped_missing_image': 0,
            'skipped_missing_code': 0,
            'skipped_lang_filter': 0,
            'skipped_errors': 0,
            'splits_processed': {},
            'start_time': None,
        }

        self._error_log_counts: Dict[str, int] = {}
        self._image_err_log_counts: Dict[str, int] = {}
        self._first_error_traceback_logged = False

        self._original_sigint = None
        self._original_sigterm = None

    def _setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, saving checkpoint...")
            self.tar_writer.close()
            self._close_metadata_stream()
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

    def _setup_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output directories created at {self.base_dir}")

    def _load_checkpoint(self) -> bool:
        if not self.checkpoint_file.exists():
            return False

        try:
            with open(self.checkpoint_file, 'r') as f:
                checkpoint = json.load(f)

            self.exported_count = checkpoint.get('exported_count', 0)
            self.current_split_index = checkpoint.get('current_split_index', 0)
            self.current_sample_index = checkpoint.get('current_sample_index', 0)
            self.stats = checkpoint.get('stats', self.stats)

            logger.info(f"Resumed from checkpoint:")
            logger.info(f"  Exported count: {self.exported_count}")
            logger.info(f"  Current split: {self.splits[self.current_split_index] if self.current_split_index < len(self.splits) else 'done'}")
            logger.info(f"  Sample index: {self.current_sample_index}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return False

    def _save_checkpoint(self) -> None:
        checkpoint = {
            'exported_count': self.exported_count,
            'current_split_index': self.current_split_index,
            'current_sample_index': self.current_sample_index,
            'stats': self.stats,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }

        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)

    def _load_dataset_split(self, split: str, start_idx: int = 0) -> Iterator[Dict[str, Any]]:
        from datasets import load_dataset

        logger.info(f"Loading {split} split from {self.DATASET_ID}...")

        if self.streaming:
            ds = load_dataset(
                self.DATASET_ID,
                split=split,
                streaming=True,
                token=self.hf_token,
            )
            if start_idx > 0:
                logger.warning(f"Streaming mode: skipping {start_idx:,} via ds.skip() (re-fetch unavoidable)")
                ds = ds.skip(start_idx)
            return iter(ds)
        else:
            ds = load_dataset(
                self.DATASET_ID,
                split=split,
                token=self.hf_token,
            )
            if start_idx > 0:
                if start_idx >= len(ds):
                    logger.warning(f"start_idx {start_idx:,} >= len(ds) {len(ds):,}; nothing to resume")
                    return iter(())
                ds = ds.select(range(start_idx, len(ds)))
            return iter(ds)

    def _process_image(self, image_obj: Any) -> Optional[bytes]:
        from PIL import Image

        try:
            if isinstance(image_obj, Image.Image):
                pil_img = image_obj
            elif isinstance(image_obj, dict):
                if 'bytes' in image_obj and image_obj['bytes']:
                    pil_img = Image.open(io.BytesIO(image_obj['bytes']))
                elif 'path' in image_obj and image_obj['path']:
                    pil_img = Image.open(image_obj['path'])
                else:
                    return None
            elif isinstance(image_obj, bytes):
                pil_img = Image.open(io.BytesIO(image_obj))
            else:
                logger.debug(f"Unknown image type: {type(image_obj)}")
                return None

            if pil_img.mode in ('RGBA', 'LA', 'P'):
                if pil_img.mode == 'P':
                    pil_img = pil_img.convert('RGBA')
                background = Image.new('RGB', pil_img.size, (255, 255, 255))
                if pil_img.mode == 'RGBA':
                    background.paste(pil_img, mask=pil_img.split()[3])
                else:
                    background.paste(pil_img, mask=pil_img.split()[1])
                pil_img = background
            elif pil_img.mode != 'RGB':
                pil_img = pil_img.convert('RGB')

            buf = io.BytesIO()
            pil_img.save(buf, format='PNG')
            return buf.getvalue()

        except Exception as e:
            err_key = type(e).__name__
            cnt = self._image_err_log_counts.get(err_key, 0) + 1
            self._image_err_log_counts[err_key] = cnt
            if cnt <= 20 or cnt % 1000 == 0:
                logger.warning(
                    f"_process_image {err_key}: {e!r} (occurrences={cnt})"
                )
            return None

    def _format_file_id(self, num: int) -> str:
        return f"{self.PREFIX}_{num:06d}"

    def _get_human_prompt(self) -> str:
        return self.HUMAN_PROMPT

    def _init_metadata_stream(self, append: bool = False) -> None:
        """Open the JSONL metadata file for writing (or appending on resume)."""
        mode = 'a' if (append and self.metadata_file.exists()) else 'w'
        self.metadata_stream = open(self.metadata_file, mode, encoding='utf-8')

    def _append_metadata_entry(self, entry: Dict[str, Any]) -> None:
        """Append one JSONL record. Buffered; flushed at checkpoint boundary."""
        if not self.metadata_stream:
            return
        self.metadata_stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _close_metadata_stream(self) -> None:
        if self.metadata_stream:
            self.metadata_stream.close()
            self.metadata_stream = None

    def _check_memory(self) -> bool:
        try:
            import psutil

            process = psutil.Process()
            mem_gb = process.memory_info().rss / (1024 ** 3)

            vm = psutil.virtual_memory()
            available_gb = vm.available / (1024 ** 3)

            if mem_gb > self.memory_limit_gb:
                logger.warning(f"Memory limit exceeded: {mem_gb:.2f}GB > {self.memory_limit_gb}GB")
                gc.collect()
                return False

            if available_gb < 2.0:
                logger.warning(f"Low system memory: {available_gb:.2f}GB available")
                gc.collect()
                return False

            return True
        except ImportError:
            return True

    def _process_sample(
        self,
        sample: Dict[str, Any],
        sample_idx: int,
    ) -> bool:
        self.stats['total_processed'] += 1

        image_obj = sample.get('image')
        html_code = sample.get('text')
        lang = sample.get('lang')

        if self.lang_filter and lang != self.lang_filter:
            self.stats['skipped_lang_filter'] += 1
            return False

        if image_obj is None:
            self.stats['skipped_missing_image'] += 1
            return False

        if not html_code or not html_code.strip():
            self.stats['skipped_missing_code'] += 1
            return False

        try:
            existing_path = self.present_indices.get(self.exported_count) if self.scan_existing_tars else None
            if existing_path is not None:
                metadata_entry = {
                    'messages': [
                        {'role': 'user', 'content': self._get_human_prompt()},
                        {'role': 'assistant', 'content': html_code},
                    ],
                    'images': [existing_path],
                }
                self._append_metadata_entry(metadata_entry)
                self.exported_count += 1
                self.stats['total_exported'] += 1
                self.stats['reused_existing'] = self.stats.get('reused_existing', 0) + 1
                return True

            image_bytes = self._process_image(image_obj)
            if image_bytes is None:
                self.stats['skipped_missing_image'] += 1
                return False

            file_id = self._format_file_id(self.exported_count)

            # MS-Swift JSONL record: messages + images
            metadata_entry = {
                'messages': [
                    {'role': 'user', 'content': self._get_human_prompt()},
                    {'role': 'assistant', 'content': html_code},
                ],
                'images': [self.tar_writer.add_image(file_id, image_bytes, self.exported_count)],
            }
            self._append_metadata_entry(metadata_entry)

            self.exported_count += 1
            self.stats['total_exported'] += 1
            return True

        except Exception as e:
            err_key = type(e).__name__
            cnt = self._error_log_counts.get(err_key, 0) + 1
            self._error_log_counts[err_key] = cnt
            if not self._first_error_traceback_logged:
                self._first_error_traceback_logged = True
                logger.warning(
                    f"First _process_sample error at sample_idx={sample_idx} "
                    f"(exported_count={self.exported_count}):\n{traceback.format_exc()}"
                )
            elif cnt <= 20 or cnt % 1000 == 0:
                logger.warning(
                    f"sample {sample_idx}: {err_key}: {e!r} (occurrences={cnt})"
                )
            self.stats['skipped_errors'] += 1
            return False

    def _reconcile_with_disk(self) -> None:
        """Non-destructive resume reconciliation.

        Fixes the two failure modes that block --resume after a crash:
          1. Tar shards with a corrupt EOF marker (readable but not
             append-openable, raising tarfile.ReadError on the next write).
          2. Checkpoint exported_count trailing the JSONL row count by up to
             one checkpoint_interval.

        The naive "trust the contiguous PNG prefix" reconcile is dangerous:
        if any middle shard is partially corrupt (real failure mode -- this
        run lost ~2.3k middle members of tar 46 from a mid-write crash), it
        produces a tiny prefix and would discard hundreds of thousands of
        valid JSONL rows. We therefore:

          - Repair corrupt shards (safe, lossless for what's recoverable).
          - Take the JSONL row count as the authoritative export count,
            since each successful export writes to tar BEFORE writing to
            JSONL, so a JSONL row implies the PNG existed at write time.
          - Refuse to truncate the JSONL. Surface any PNG-vs-JSONL gap as
            a loud warning so the user can backfill or filter downstream.
          - Refuse to silently shrink exported_count (a shorter-than-
            checkpoint JSONL means someone touched the file out-of-band).
        """
        logger.info("Reconcile: validating + repairing existing tar shards...")
        validate_and_repair_shards(self.base_dir, self.PREFIX)

        present = scan_present_indices(self.base_dir, self.PREFIX)
        if present:
            idxs = sorted(present.keys())
            png_unique = len(present)
            png_max = idxs[-1]
            png_contig = 0
            for i, idx in enumerate(idxs):
                if idx == i:
                    png_contig = i + 1
                else:
                    break
            missing = sorted(set(range(png_max + 1)) - present.keys())
        else:
            png_unique = png_max = png_contig = 0
            missing = []

        jsonl_count = 0
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                for _ in f:
                    jsonl_count += 1

        logger.info(
            "Reconcile state:\n"
            f"  checkpoint.exported_count = {self.exported_count}\n"
            f"  jsonl rows                = {jsonl_count}\n"
            f"  tar unique PNG indices    = {png_unique}\n"
            f"  tar highest index         = {png_max}\n"
            f"  tar contiguous from 0     = {png_contig}\n"
            f"  tar missing in [0,max]    = {len(missing)}"
        )

        if jsonl_count < self.exported_count:
            raise RuntimeError(
                f"Reconcile abort: JSONL has {jsonl_count} rows but checkpoint "
                f"claims {self.exported_count} exports. JSONL was modified "
                f"outside this pipeline; investigate before resuming."
            )

        if missing:
            ranges: List[str] = []
            run_start = missing[0]
            prev = missing[0]
            for i in missing[1:]:
                if i != prev + 1:
                    ranges.append(f"[{run_start},{prev}]({prev - run_start + 1})")
                    run_start = i
                prev = i
            ranges.append(f"[{run_start},{prev}]({prev - run_start + 1})")
            logger.warning(
                f"Reconcile: {len(missing)} PNG indices missing from tars but "
                f"referenced by JSONL. Ranges (start,end,count): "
                f"{', '.join(ranges[:10])}"
                + (f" ... ({len(ranges)} ranges total)" if len(ranges) > 10 else "")
            )
            logger.warning(
                "Reconcile: NOT truncating JSONL. Dangling rows will fail to "
                "load PNGs during training -- filter them downstream (drop "
                "rows whose 'images' path is missing from tars) or backfill "
                "the gap with a targeted re-fetch."
            )

        new_count = jsonl_count
        if new_count != self.exported_count:
            logger.warning(
                f"Reconcile: advancing exported_count "
                f"{self.exported_count} -> {new_count} to match JSONL"
            )
            self.exported_count = new_count
            self.stats['total_exported'] = max(
                self.stats.get('total_exported', 0), new_count
            )

    def _create_100k_subset(self) -> None:
        """Create a 100k subset JSONL file if we have more than 100k samples."""
        if self.exported_count <= 100000:
            if self.metadata_file.exists():
                import shutil
                shutil.copy(self.metadata_file, self.metadata_100k_file)
                logger.info(f"Created 100k subset file: {self.metadata_100k_file}")
            return

        logger.info("Creating 100k subset metadata file...")
        try:
            count = 0
            with open(self.metadata_file, 'r', encoding='utf-8') as src, \
                 open(self.metadata_100k_file, 'w', encoding='utf-8') as dst:
                for line in src:
                    if count >= 100000:
                        break
                    dst.write(line)
                    count += 1
            logger.info(f"Created 100k subset with {count} entries")
        except Exception as e:
            logger.warning(f"Failed to create 100k subset: {e}")

    def _print_summary(self) -> None:
        elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
        rate = self.stats['total_exported'] / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 60)
        print("WebCode2M Export Complete (MS-Swift Format)")
        print("=" * 60)
        print(f"Total processed: {self.stats['total_processed']:,}")
        print(f"Total exported:  {self.stats['total_exported']:,}")
        print(f"Skipped (no image):   {self.stats['skipped_missing_image']:,}")
        print(f"Skipped (no code):    {self.stats['skipped_missing_code']:,}")
        print(f"Skipped (lang filter):{self.stats['skipped_lang_filter']:,}")
        print(f"Skipped (errors):     {self.stats['skipped_errors']:,}")
        print(f"Processing rate: {rate:.2f} samples/sec")
        print(f"Elapsed time: {elapsed:.1f}s")
        print(f"\nOutput files:")
        print(f"  Tar shards: {self.base_dir}")
        print(f"  Metadata: {self.metadata_file}")
        if self.metadata_100k_file.exists():
            print(f"  100k Subset: {self.metadata_100k_file}")
        print("=" * 60)

    def run(
        self,
        max_samples: Optional[int] = None,
        resume: bool = True,
    ) -> Dict[str, Any]:
        from tqdm import tqdm

        self.stats['start_time'] = time.time()

        self._setup_signal_handlers()
        self._setup_directories()

        if self.scan_existing_tars:
            logger.info("Validating and repairing existing tar shards...")
            validate_and_repair_shards(self.base_dir, self.PREFIX)
            logger.info("Scanning existing tar shards for already-exported samples...")
            self.present_indices = scan_present_indices(self.base_dir, self.PREFIX)
            if self.present_indices:
                idxs = sorted(self.present_indices.keys())
                logger.info(
                    f"Found {len(idxs):,} existing PNGs across tars "
                    f"(min={idxs[0]}, max={idxs[-1]})"
                )
            else:
                logger.info("No existing tar shards found; will fetch all samples from scratch.")
            self.exported_count = 0
            self.current_split_index = 0
            self.current_sample_index = 0
            is_resuming = False
        else:
            is_resuming = resume and self._load_checkpoint()
            if is_resuming:
                self._reconcile_with_disk()

        self._init_metadata_stream(append=is_resuming)

        logger.info(f"WebCode2M Dataset Fetcher (MS-Swift Format)")
        logger.info(f"  Dataset: {self.DATASET_ID}")
        logger.info(f"  Output: {self.base_dir}")
        logger.info(f"  Splits: {self.splits}")
        logger.info(f"  Max samples: {max_samples or 'all'}")
        logger.info(f"  Streaming: {self.streaming}")
        logger.info(f"  Scan existing tars: {self.scan_existing_tars}")
        if self.lang_filter:
            logger.info(f"  Language filter: {self.lang_filter}")

        try:
            for split_idx in range(self.current_split_index, len(self.splits)):
                split = self.splits[split_idx]
                self.current_split_index = split_idx

                logger.info(f"\nProcessing split: {split}")

                start_idx = self.current_sample_index if split_idx == self.current_split_index and is_resuming else 0

                dataset_iter = self._load_dataset_split(split, start_idx=start_idx)

                pbar = tqdm(
                    desc=f"Processing {split}",
                    unit="samples",
                    initial=start_idx,
                )

                sample_idx = start_idx
                for sample in dataset_iter:
                    self.current_sample_index = sample_idx

                    if max_samples and self.exported_count >= max_samples:
                        logger.info(f"Reached max samples limit: {max_samples}")
                        pbar.close()
                        break

                    if sample_idx % 100 == 0:
                        self._check_memory()

                    self._process_sample(sample, sample_idx)

                    pbar.update(1)
                    postfix = {
                        'exported': self.exported_count,
                        'reused': self.stats.get('reused_existing', 0),
                        'err': self.stats['skipped_errors'],
                        'noimg': self.stats['skipped_missing_image'],
                        'nocode': self.stats['skipped_missing_code'],
                    }
                    if self.lang_filter:
                        postfix['lang'] = self.stats['skipped_lang_filter']
                    pbar.set_postfix(postfix)

                    if sample_idx > 0 and sample_idx % self.checkpoint_interval == 0:
                        # Flush tar + JSONL together with the checkpoint so
                        # all three are durable at the same boundary on
                        # Lustre. Reconcile recovers any drift up to this
                        # window on a crashed --resume.
                        self.tar_writer.flush()
                        if self.metadata_stream is not None:
                            self.metadata_stream.flush()
                        self._save_checkpoint()
                        gc.collect()

                    sample_idx += 1

                pbar.close()
                self.stats['splits_processed'][split] = sample_idx

                self.current_sample_index = 0
                is_resuming = False

                if max_samples and self.exported_count >= max_samples:
                    break

            self.tar_writer.close()
            self._close_metadata_stream()
            self._save_checkpoint()

            self._create_100k_subset()

            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()

            self._restore_signal_handlers()

            issues = verify_shard_sequence(self.base_dir, self.PREFIX)
            if issues:
                logger.warning("Shard sequence check found issues:")
                for msg in issues:
                    logger.warning(f"  - {msg}")
            else:
                logger.info("Shard sequence check passed: each tar has 5000 sequential members (last shard may be partial).")

            self._print_summary()
            return self.stats

        except KeyboardInterrupt:
            logger.info("\nInterrupted by user, saving checkpoint...")
            self.tar_writer.close()
            self._close_metadata_stream()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

        except Exception as e:
            logger.error(f"Error during processing: {e}")
            self.tar_writer.close()
            self._close_metadata_stream()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

    def verify_output(self) -> Dict[str, Any]:
        """Verify integrity of existing JSONL output files."""
        stats = {
            'total_images': 0,
            'metadata_entries': 0,
            'metadata_100k_entries': 0,
            'metadata_valid': False,
            'metadata_100k_valid': False,
            'mismatched_files': [],
            'sample_entries': [],
        }

        if self.base_dir.exists():
            stats['total_images'] = count_tar_png_members(self.base_dir)

        if self.metadata_file.exists():
            valid, count, samples = _count_jsonl(self.metadata_file, sample_n=3)
            stats['metadata_entries'] = count if valid else -1
            stats['metadata_valid'] = valid
            stats['sample_entries'] = samples

        if self.metadata_100k_file.exists():
            valid, count, _ = _count_jsonl(self.metadata_100k_file, sample_n=0)
            stats['metadata_100k_entries'] = count if valid else -1
            stats['metadata_100k_valid'] = valid

        if stats['metadata_valid'] and stats['metadata_entries'] != stats['total_images']:
            stats['mismatched_files'].append(
                f"Metadata entries ({stats['metadata_entries']}) != Image count ({stats['total_images']})"
            )

        if stats['metadata_valid'] and stats['sample_entries']:
            for i, entry in enumerate(stats['sample_entries']):
                if 'messages' not in entry:
                    stats['mismatched_files'].append(f"Entry #{i} missing 'messages' field")
                elif len(entry['messages']) != 2:
                    stats['mismatched_files'].append(
                        f"Entry #{i} has {len(entry['messages'])} messages, expected 2"
                    )
                if 'images' not in entry or not entry['images']:
                    stats['mismatched_files'].append(f"Entry #{i} missing/empty 'images' field")

        return stats


def _count_jsonl(path: Path, sample_n: int = 3):
    """Count lines in a JSONL file and return up to `sample_n` parsed sample entries.

    Returns (valid, count, samples).
    """
    try:
        count = 0
        samples: List[Dict[str, Any]] = []
        with open(path, 'r', encoding='utf-8') as f:
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
    parser = argparse.ArgumentParser(
        description="Fetch WebCode2M dataset and export to MS-Swift JSONL format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all samples from all splits
  python web2m_fetcher.py -o ./output

  # Export first 100,000 samples
  python web2m_fetcher.py -o ./output -n 100000

  # Export only train split
  python web2m_fetcher.py -o ./output --splits train

  # Resume interrupted processing
  python web2m_fetcher.py -o ./output --resume

  # Filter by language
  python web2m_fetcher.py -o ./output --lang en

  # Verify existing output
  python web2m_fetcher.py -o ./output --verify

Output structure:
  {output}/ms_swift/web100k/
      images-00000.tar, images-00001.tar, ...
      meta_data_web.jsonl
      meta_data_web_100k.jsonl
"""
    )

    parser.add_argument("-o", "--output-dir", type=str, required=True,
                        help="Base output directory (required)")
    parser.add_argument("-n", "--num-samples", type=int, default=None,
                        help="Maximum number of samples to export (default: all)")
    parser.add_argument("--splits", type=str, nargs='+', default=['train'],
                        help="Dataset splits to process (default: train)")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Batch size for progress updates (default: 100)")
    parser.add_argument("--checkpoint-interval", type=int, default=1000,
                        help="Samples between checkpoint saves (default: 1000)")
    parser.add_argument("--no-streaming", action="store_true",
                        help="Disable streaming mode (downloads entire dataset)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint if exists")
    parser.add_argument("--no-resume", action="store_true",
                        help="Start fresh, ignoring existing checkpoint")
    parser.add_argument("--scan-tars", action="store_true",
                        help="Scan existing tar shards and only fetch samples whose "
                             "{prefix}_NNNNNN.png is missing. Iterates dataset from sample 0, "
                             "rebuilds the metadata jsonl to match all on-disk tars, and "
                             "auto-repairs corrupt shards.")
    parser.add_argument("--hf-token", type=str, default=None,
                        help="HuggingFace API token (optional)")
    parser.add_argument("--memory-limit", type=float, default=50.0,
                        help="Memory limit in GB (default: 50)")
    parser.add_argument("--lang", type=str, default=None,
                        help="Filter by language code (e.g., 'en')")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose logging")
    parser.add_argument("--verify", action="store_true",
                        help="Verify existing output files and exit")

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    resume = not args.no_resume
    if args.resume:
        resume = True

    fetcher = WebCode2MFetcher(
        output_dir=args.output_dir,
        splits=args.splits,
        batch_size=args.batch_size,
        checkpoint_interval=args.checkpoint_interval,
        memory_limit_gb=args.memory_limit,
        hf_token=args.hf_token,
        streaming=not args.no_streaming,
        lang_filter=args.lang,
        scan_existing_tars=args.scan_tars,
    )

    if args.verify:
        stats = fetcher.verify_output()
        print(json.dumps(stats, indent=2))
        return

    try:
        stats = fetcher.run(
            max_samples=args.num_samples,
            resume=resume,
        )

        if stats.get('skipped_errors', 0) > 0:
            logger.warning(f"Completed with {stats['skipped_errors']} errors")

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
