#!/usr/bin/env python3
"""
Web2Code Dataset Fetcher for MS-Swift format.

Fetches the MBZUAI/Web2Code dataset from HuggingFace and exports
to MS-Swift JSONL format for training vision-language models.

Filters:
    - Only keeps samples where GPT response starts with <html> or <!DOCTYPE html>
    - Only keeps samples where GPT response ends with </html>
    - Only keeps single-round conversations (1 human + 1 gpt)

Output structure:
    output/ms_swift/web2code/
        images-00000.tar, images-00001.tar, ...
        meta_data_web2code.jsonl             # Original prompts from dataset
        meta_data_web2code_fixed.jsonl       # Fixed prompt template

Each JSONL line has the shape:
    {"messages": [{"role": "user", "content": "<image>\\n..."},
                  {"role": "assistant", "content": "<html>..."}],
     "images": ["images-00000.tar/web2code_000000.png"]}

Setup:
    1. Download images: wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code_image.zip
    2. Download data: wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code.json

Usage:
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json -n 100
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --resume
    python ms_swift_format_process/baseline_prepare/web2code_fetcher.py -o /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code --images-zip /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code/Web2Code_image.zip --json-data /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code/Web2Code.json --workers 8 

running
python ms_swift_format_process/baseline_prepare/web2code_fetcher.py -o /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code --images-zip /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code/Web2Code_image.zip --json-data /home/liu282/scratch3/projects/vision_to_code/dataset/baseline/web2code/Web2Code.json --workers 8

Note:
    The HuggingFace load_dataset() only returns ~100 sample preview.
    For the full dataset (800k+ samples), download and use Web2Code.json with --json-data.
"""

import argparse
import gc
import io
import json
import logging
import multiprocessing as mp
import os
import re
import signal
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

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


def _decode_encode_png_bytes(raw_bytes: bytes) -> Optional[bytes]:
    """Decode arbitrary image bytes, composite onto white, re-encode as RGB PNG.

    Top-level so it is picklable for multiprocessing.Pool.
    Returns None on any failure (caller treats as missing image).
    """
    from PIL import Image

    try:
        pil_img = Image.open(io.BytesIO(raw_bytes))

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
    except Exception:
        return None


class Web2CodeFetcher:
    """Fetches Web2Code dataset and exports to MS-Swift JSONL format."""

    DATASET_ID = "MBZUAI/Web2Code"
    PREFIX = "web2code"
    CATEGORY_NUM = "web2code"

    HUMAN_PROMPT = """<image>
Drawing from the webpage screenshot, create corresponding HTML and CSS code.
"""

    HTML_START_PATTERN = re.compile(r'^\s*(<html|<!DOCTYPE\s+html)', re.IGNORECASE)
    HTML_END_PATTERN = re.compile(r'</html>\s*$', re.IGNORECASE)

    def __init__(
        self,
        output_dir: str,
        images_zip_path: str,
        json_data_path: Optional[str] = None,
        splits: Optional[List[str]] = None,
        batch_size: int = 100,
        checkpoint_interval: int = 1000,
        memory_limit_gb: float = 50.0,
        hf_token: Optional[str] = None,
        streaming: bool = True,
        scan_existing_tars: bool = False,
        workers: int = 1,
    ):
        self.output_dir = Path(output_dir)
        self.images_zip_path = Path(images_zip_path)
        self.json_data_path = Path(json_data_path) if json_data_path else None
        self.zip_file = None
        self.splits = splits or ['train']
        self.batch_size = batch_size
        self.checkpoint_interval = checkpoint_interval
        self.memory_limit_gb = memory_limit_gb
        self.hf_token = hf_token
        self.streaming = streaming
        self.scan_existing_tars = scan_existing_tars
        self.workers = max(1, int(workers))
        self.present_indices: Dict[int, str] = {}

        self.base_dir = self.output_dir / 'ms_swift' / self.CATEGORY_NUM
        self.tar_writer = TarShardWriter(self.base_dir)

        self.checkpoint_file = self.base_dir / '.web2code_checkpoint.json'
        self.metadata_file = self.base_dir / f'meta_data_{self.PREFIX}.jsonl'
        self.metadata_fixed_file = self.base_dir / f'meta_data_{self.PREFIX}_fixed.jsonl'

        self.exported_count = 0
        self.current_split_index = 0
        self.current_sample_index = 0
        self.metadata_stream = None
        self.metadata_fixed_stream = None
        self._pending: List[Tuple[Dict[str, str], bytes]] = []
        self._pool: Optional[mp.pool.Pool] = None

        self.stats = {
            'total_processed': 0,
            'total_exported': 0,
            'skipped_missing_image': 0,
            'skipped_missing_code': 0,
            'skipped_multi_round': 0,
            'skipped_invalid_html': 0,
            'skipped_errors': 0,
            'splits_processed': {},
            'start_time': None,
        }

        self._original_sigint = None
        self._original_sigterm = None

    def _setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, flushing pending and saving checkpoint...")
            try:
                self._flush_pending()
            except Exception as e:
                logger.warning(f"Failed to flush pending batch on signal: {e}")
            self._close_zip()
            self.tar_writer.close()
            self._close_metadata_streams()
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

    def _open_zip(self) -> None:
        self.zip_file = zipfile.ZipFile(self.images_zip_path, 'r')
        logger.info(f"Opened ZIP file: {self.images_zip_path}")

    def _close_zip(self) -> None:
        if self.zip_file:
            self.zip_file.close()
            self.zip_file = None

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

    def _iter_json_streaming(self, path: Path, start_idx: int = 0) -> Iterator[Dict[str, Any]]:
        """Stream a JSON array file one record at a time (bounded memory).

        Uses ijson if available (C backend recommended). Falls back to json.load
        with a loud warning if not — that path spikes memory and may be killed
        by admin policies on login nodes.
        """
        try:
            import ijson  # type: ignore
        except ImportError:
            logger.warning(
                "ijson not installed; falling back to json.load (HIGH MEMORY, ~10-15GB for "
                "Web2Code.json). Install with: pip install ijson"
            )
            with open(path, 'r') as f:
                data = json.load(f)
            total = len(data)
            logger.info(f"Loaded {total:,} samples from JSON file")
            if start_idx > 0:
                if start_idx >= total:
                    logger.warning(f"start_idx {start_idx:,} >= total {total:,}; nothing to resume")
                    return
                logger.info(f"Resuming at sample {start_idx:,} (slice skip)")
                data = data[start_idx:]
            for item in data:
                yield item
            return

        logger.info(f"Streaming JSON from {path} via ijson (bounded memory).")
        if start_idx > 0:
            logger.info(f"Will skip first {start_idx:,} records to resume.")
        skipped = 0
        yielded = 0
        with open(path, 'rb') as f:
            for item in ijson.items(f, 'item'):
                if skipped < start_idx:
                    skipped += 1
                    continue
                yielded += 1
                if yielded == 1:
                    logger.info("First record streamed; main loop is now consuming.")
                yield item

    def _load_dataset_split(self, split: str, start_idx: int = 0) -> Iterator[Dict[str, Any]]:
        if self.json_data_path and self.json_data_path.exists():
            return self._iter_json_streaming(self.json_data_path, start_idx=start_idx)

        logger.warning("Using HuggingFace preview (limited to ~100 samples). "
                      "For full dataset, download Web2Code.json and use --json-data")

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
            logger.debug(f"Image processing error: {e}")
            return None

    def _load_image_from_zip(self, image_path: str) -> Optional[bytes]:
        from PIL import Image

        try:
            image_data = self.zip_file.read(image_path)
            pil_img = Image.open(io.BytesIO(image_data))

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

        except KeyError:
            return None
        except Exception as e:
            logger.debug(f"Image loading error for {image_path}: {e}")
            return None

    def _format_file_id(self, num: int) -> str:
        return f"{self.PREFIX}_{num:06d}"

    def _is_valid_html_response(self, gpt_value: str) -> bool:
        if not gpt_value or not isinstance(gpt_value, str):
            return False

        has_valid_start = bool(self.HTML_START_PATTERN.match(gpt_value))
        has_valid_end = bool(self.HTML_END_PATTERN.search(gpt_value))

        return has_valid_start and has_valid_end

    def _is_single_round_conversation(self, conversations: List[Dict[str, Any]]) -> bool:
        """Check upstream Web2Code samples: exactly 2 entries, first 'human', second 'gpt'."""
        if not conversations or len(conversations) != 2:
            return False

        first_from = conversations[0].get('from', '').lower()
        second_from = conversations[1].get('from', '').lower()

        return first_from == 'human' and second_from == 'gpt'

    def _init_metadata_streams(self, append: bool = False) -> None:
        """Open both JSONL metadata streams (original + fixed prompt variants)."""
        for metadata_file, stream_attr in [
            (self.metadata_file, 'metadata_stream'),
            (self.metadata_fixed_file, 'metadata_fixed_stream'),
        ]:
            mode = 'a' if (append and metadata_file.exists()) else 'w'
            stream = open(metadata_file, mode, encoding='utf-8')
            setattr(self, stream_attr, stream)

    def _append_metadata_entries(
        self,
        entry_original: Dict[str, Any],
        entry_fixed: Dict[str, Any]
    ) -> None:
        if self.metadata_stream:
            self.metadata_stream.write(json.dumps(entry_original, ensure_ascii=False) + '\n')
            self.metadata_stream.flush()

        if self.metadata_fixed_stream:
            self.metadata_fixed_stream.write(json.dumps(entry_fixed, ensure_ascii=False) + '\n')
            self.metadata_fixed_stream.flush()

    def _close_metadata_streams(self) -> None:
        for stream_attr in ['metadata_stream', 'metadata_fixed_stream']:
            stream = getattr(self, stream_attr, None)
            if stream:
                stream.close()
                setattr(self, stream_attr, None)

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

    def _check_sample(self, sample: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Run filter checks; return prepared prompt fields or None if filtered out."""
        self.stats['total_processed'] += 1

        conversations = sample.get('conversations', [])
        if not self._is_single_round_conversation(conversations):
            self.stats['skipped_multi_round'] += 1
            return None

        gpt_value = conversations[1].get('value', '')
        if not self._is_valid_html_response(gpt_value):
            self.stats['skipped_invalid_html'] += 1
            return None

        image_path_str = sample.get('image')
        if not image_path_str:
            self.stats['skipped_missing_image'] += 1
            return None

        return {
            'image_path': image_path_str,
            'human_value': conversations[0].get('value', ''),
            'gpt_value': gpt_value,
        }

    def _write_pair(self, prepped: Dict[str, str], image_ref: List[str]) -> None:
        gpt_value = prepped['gpt_value']
        human_value = prepped['human_value']

        entry_original = {
            'messages': [
                {'role': 'user', 'content': human_value},
                {'role': 'assistant', 'content': gpt_value},
            ],
            'images': image_ref,
        }
        entry_fixed = {
            'messages': [
                {'role': 'user', 'content': self.HUMAN_PROMPT},
                {'role': 'assistant', 'content': gpt_value},
            ],
            'images': image_ref,
        }
        self._append_metadata_entries(entry_original, entry_fixed)
        self.exported_count += 1
        self.stats['total_exported'] += 1

    def _emit_sample(self, prepped: Dict[str, str], png_bytes: bytes) -> None:
        file_id = self._format_file_id(self.exported_count)
        image_ref = [self.tar_writer.add_image(file_id, png_bytes, self.exported_count)]
        self._write_pair(prepped, image_ref)

    def _emit_sample_reused(self, prepped: Dict[str, str], existing_path: str) -> None:
        self._write_pair(prepped, [existing_path])
        self.stats['reused_existing'] = self.stats.get('reused_existing', 0) + 1

    def _flush_pending(self) -> None:
        """Decode any pending raw image bytes (via pool if active) and emit."""
        if not self._pending:
            return
        raw_list = [rb for _, rb in self._pending]
        if self._pool is not None:
            chunk = max(1, len(raw_list) // (self.workers * 4))
            png_list = self._pool.map(_decode_encode_png_bytes, raw_list, chunksize=chunk)
        else:
            png_list = [_decode_encode_png_bytes(rb) for rb in raw_list]
        for (prepped, _), png_bytes in zip(self._pending, png_list):
            if png_bytes is None:
                self.stats['skipped_missing_image'] += 1
                continue
            self._emit_sample(prepped, png_bytes)
        self._pending.clear()

    def _process_sample(
        self,
        sample: Dict[str, Any],
        sample_idx: int,
    ) -> bool:
        prepped = self._check_sample(sample)
        if prepped is None:
            return False

        try:
            existing_path = self.present_indices.get(self.exported_count) if self.scan_existing_tars else None
            if existing_path is not None:
                self._emit_sample_reused(prepped, existing_path)
                return True

            image_bytes = self._load_image_from_zip(prepped['image_path'])
            if image_bytes is None:
                self.stats['skipped_missing_image'] += 1
                return False

            self._emit_sample(prepped, image_bytes)
            return True

        except Exception as e:
            logger.debug(f"Error processing sample {sample_idx}: {e}")
            self.stats['skipped_errors'] += 1
            return False

    def _print_summary(self) -> None:
        elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
        rate = self.stats['total_exported'] / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 60)
        print("Web2Code Export Complete (MS-Swift Format)")
        print("=" * 60)
        print(f"Total processed: {self.stats['total_processed']:,}")
        print(f"Total exported:  {self.stats['total_exported']:,}")
        print(f"Skipped (no image):      {self.stats['skipped_missing_image']:,}")
        print(f"Skipped (no code):       {self.stats['skipped_missing_code']:,}")
        print(f"Skipped (multi-round):   {self.stats['skipped_multi_round']:,}")
        print(f"Skipped (invalid HTML):  {self.stats['skipped_invalid_html']:,}")
        print(f"Skipped (errors):        {self.stats['skipped_errors']:,}")
        print(f"Processing rate: {rate:.2f} samples/sec")
        print(f"Elapsed time: {elapsed:.1f}s")
        print(f"\nOutput files:")
        print(f"  Tar shards: {self.base_dir}")
        print(f"  Metadata (original): {self.metadata_file}")
        print(f"  Metadata (fixed): {self.metadata_fixed_file}")
        print("=" * 60)

    def run(
        self,
        max_samples: Optional[int] = None,
        resume: bool = True,
    ) -> Dict[str, Any]:
        from tqdm import tqdm

        self.stats['start_time'] = time.time()

        effective_streaming = self.streaming and (max_samples is None)
        if max_samples and self.streaming:
            logger.info(f"Disabling streaming mode to ensure access to enough samples for -n {max_samples}")
        self.streaming = effective_streaming

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

        self._init_metadata_streams(append=is_resuming)

        self._open_zip()

        use_parallel = self.workers > 1 and not self.scan_existing_tars
        if self.workers > 1 and self.scan_existing_tars:
            logger.warning("--scan-tars is set; ignoring --workers and running serial path.")

        logger.info(f"Web2Code Dataset Fetcher (MS-Swift Format)")
        logger.info(f"  Dataset: {self.DATASET_ID}")
        logger.info(f"  Output: {self.base_dir}")
        logger.info(f"  Splits: {self.splits}")
        logger.info(f"  Max samples: {max_samples or 'all'}")
        logger.info(f"  Streaming: {self.streaming}")
        logger.info(f"  Filters: single-round + valid HTML structure")
        logger.info(f"  Scan existing tars: {self.scan_existing_tars}")
        logger.info(f"  Workers: {self.workers} ({'parallel' if use_parallel else 'serial'})")

        if use_parallel:
            self._pool = mp.Pool(processes=self.workers)
            batch_flush_size = max(64, self.workers * 16)
        else:
            batch_flush_size = 0  # unused

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

                    if max_samples and (self.exported_count + len(self._pending)) >= max_samples:
                        self._flush_pending()
                        if self.exported_count >= max_samples:
                            logger.info(f"Reached export limit: {max_samples} samples exported")
                            pbar.close()
                            break

                    if sample_idx % 100 == 0:
                        self._check_memory()

                    if use_parallel:
                        prepped = self._check_sample(sample)
                        if prepped is not None:
                            try:
                                raw_bytes = self.zip_file.read(prepped['image_path'])
                                self._pending.append((prepped, raw_bytes))
                            except KeyError:
                                self.stats['skipped_missing_image'] += 1
                            except Exception as e:
                                logger.debug(f"Zip read error {prepped['image_path']}: {e}")
                                self.stats['skipped_errors'] += 1

                        if len(self._pending) >= batch_flush_size:
                            self._flush_pending()
                    else:
                        self._process_sample(sample, sample_idx)

                    pbar.update(1)
                    pbar.set_postfix({
                        'exported': self.exported_count,
                        'pending': len(self._pending),
                        'reused': self.stats.get('reused_existing', 0),
                        'skipped': self.stats['total_processed'] - self.stats['total_exported'] - len(self._pending),
                    })

                    if sample_idx > 0 and sample_idx % self.checkpoint_interval == 0:
                        self._flush_pending()
                        self._save_checkpoint()
                        gc.collect()

                    sample_idx += 1
                else:
                    pbar.close()
                    if max_samples and self.exported_count < max_samples:
                        logger.warning(f"Split '{split}' exhausted after {sample_idx} samples. Exported {self.exported_count}/{max_samples} samples so far.")

                # Flush trailing batch before moving to next split
                self._flush_pending()

                self.stats['splits_processed'][split] = sample_idx

                self.current_sample_index = 0
                is_resuming = False

                if max_samples and self.exported_count >= max_samples:
                    break

            self._flush_pending()

            self._close_zip()
            self.tar_writer.close()
            self._close_metadata_streams()
            self._save_checkpoint()

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
            try:
                self._flush_pending()
            except Exception:
                pass
            self._close_zip()
            self.tar_writer.close()
            self._close_metadata_streams()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

        except Exception as e:
            logger.error(f"Error during processing: {e}")
            self._close_zip()
            self.tar_writer.close()
            self._close_metadata_streams()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

        finally:
            if self._pool is not None:
                self._pool.close()
                self._pool.join()
                self._pool = None

    def verify_output(self) -> Dict[str, Any]:
        stats = {
            'total_images': 0,
            'metadata_entries': 0,
            'metadata_fixed_entries': 0,
            'metadata_valid': False,
            'metadata_fixed_valid': False,
            'mismatched_files': [],
            'sample_entries': [],
            'html_validation': {'valid': 0, 'invalid': 0},
        }

        if self.base_dir.exists():
            stats['total_images'] = count_tar_png_members(self.base_dir)

        file_config = [
            (self.metadata_file, 'metadata_entries', 'metadata_valid', True),
            (self.metadata_fixed_file, 'metadata_fixed_entries', 'metadata_fixed_valid', False),
        ]
        for file_path, entries_key, valid_key, is_primary in file_config:
            if not file_path.exists():
                continue
            valid, count, samples = _count_jsonl(file_path, sample_n=3 if is_primary else 0)
            stats[entries_key] = count if valid else -1
            stats[valid_key] = valid

            if is_primary and valid:
                stats['sample_entries'] = samples
                # Validate HTML in assistant responses for first N entries
                validated = 0
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        messages = entry.get('messages', [])
                        if len(messages) >= 2:
                            asst_content = messages[1].get('content', '')
                            if self._is_valid_html_response(asst_content):
                                stats['html_validation']['valid'] += 1
                            else:
                                stats['html_validation']['invalid'] += 1
                        validated += 1
                        if validated >= 100:
                            break

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
        description="Fetch Web2Code dataset and export to MS-Swift JSONL format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Setup:
  1. Download images: wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code_image.zip
  2. Download data:   wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code.json

Note:
  The HuggingFace load_dataset() only returns ~100 sample preview.
  For the full dataset (800k+ samples), use --json-data with Web2Code.json.

Examples:
  # Export 100 samples with full dataset access (recommended)
  python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json -n 100

  # Export all samples from full dataset
  python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json

  # Export only train split
  python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json --splits train

  # Resume interrupted processing
  python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json --resume

  # Verify existing output
  python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --verify

Output structure:
  {output}/ms_swift/web2code/
      images-00000.tar, images-00001.tar, ...
      meta_data_web2code.jsonl              (original prompts)
      meta_data_web2code_fixed.jsonl        (fixed prompts)

Filters applied:
  - Only single-round conversations (1 human + 1 gpt)
  - GPT response must start with <html> or <!DOCTYPE html>
  - GPT response must end with </html>
"""
    )

    parser.add_argument("-o", "--output-dir", type=str, required=True,
                        help="Base output directory (required)")
    parser.add_argument("--images-zip", type=str, required=True,
                        help="Path to Web2Code_image.zip file (required)")
    parser.add_argument("--json-data", type=str, default=None,
                        help="Path to Web2Code.json file (download from HuggingFace for full dataset of 800k+ samples)")
    parser.add_argument("-n", "--num-samples", type=int, default=None,
                        help="Maximum number of samples to EXPORT (continues processing until this many pass filters)")
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
    parser.add_argument("--hf-token", type=str, default="xxx",
                        help="HuggingFace API token (optional)")
    parser.add_argument("--memory-limit", type=float, default=50.0,
                        help="Memory limit in GB (default: 50)")
    parser.add_argument("-w", "--workers", type=int, default=1,
                        help="Worker processes for parallel PNG decode/re-encode "
                             "(default: 1 = serial). Recommended: 8-16 on multi-core hosts. "
                             "Disabled automatically when --scan-tars is set.")
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

    fetcher = Web2CodeFetcher(
        output_dir=args.output_dir,
        images_zip_path=args.images_zip,
        json_data_path=args.json_data,
        splits=args.splits,
        batch_size=args.batch_size,
        checkpoint_interval=args.checkpoint_interval,
        memory_limit_gb=args.memory_limit,
        hf_token=args.hf_token,
        streaming=not args.no_streaming,
        scan_existing_tars=args.scan_tars,
        workers=args.workers,
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
