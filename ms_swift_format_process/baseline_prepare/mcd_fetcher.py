#!/usr/bin/env python3
"""
MCD (MultimodalCodingDataset) Fetcher for MS-Swift format.

Fetches the lingjie23/MultimodalCodingDataset from HuggingFace and exports
HTML-category samples to MS-Swift JSONL format for training vision-language models.

Output structure:
    output/ms_swift/{category-name}/   (default: McD100k)
        images-00000.tar, images-00001.tar, ...
        meta_data_web.jsonl              # Fixed prompts
        meta_data_web_original.jsonl     # Original prompts from dataset
        meta_data_web_100k.jsonl         # 100k subset (fixed)
        meta_data_web_original_100k.jsonl  # 100k subset (original)

Each JSONL line has the shape:
    {"messages": [{"role": "user", "content": "<image>\\n..."},
                  {"role": "assistant", "content": "<html>..."}],
     "images": ["images-00000.tar/web_000000.png"]}

Setup:
    Download images ZIP from HuggingFace or use existing mcd_images.zip

Usage:
    python mcd_fetcher.py -o ./output --images-zip /path/mcd_images.zip --json-data /path/mcd_598k.json
    python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip -n 100000
    python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --category-name web2code
    python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --splits train --resume
    python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --verify
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
import zipfile
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


class MCDFetcher:
    """Fetches MCD dataset and exports HTML samples to MS-Swift JSONL format."""

    DATASET_ID = "lingjie23/MultimodalCodingDataset"
    PREFIX = "web"
    CATEGORY_NUM = "McD100k"
    HTML_CATEGORY = "html"

    HUMAN_PROMPT = """<image>
Drawing from the webpage screenshot, create corresponding HTML and CSS code.
"""

    def __init__(
        self,
        output_dir: str,
        images_zip_path: str,
        json_data_path: Optional[str] = None,
        category_name: Optional[str] = None,
        splits: Optional[List[str]] = None,
        batch_size: int = 100,
        checkpoint_interval: int = 1000,
        memory_limit_gb: float = 50.0,
        hf_token: Optional[str] = None,
        streaming: bool = True,
        scan_existing_tars: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.images_zip_path = Path(images_zip_path)
        self.json_data_path = Path(json_data_path) if json_data_path else None
        self.zip_file = None
        self.category_num = category_name or self.CATEGORY_NUM
        self.splits = splits or ['train']
        self.batch_size = batch_size
        self.checkpoint_interval = checkpoint_interval
        self.memory_limit_gb = memory_limit_gb
        self.hf_token = hf_token
        self.streaming = streaming
        self.scan_existing_tars = scan_existing_tars
        self.present_indices: Dict[int, str] = {}

        self.base_dir = self.output_dir / 'ms_swift' / self.category_num
        self.tar_writer = TarShardWriter(self.base_dir)

        self.checkpoint_file = self.base_dir / '.mcd_checkpoint.json'
        self.metadata_file = self.base_dir / f'meta_data_{self.PREFIX}.jsonl'
        self.metadata_original_file = self.base_dir / f'meta_data_{self.PREFIX}_original.jsonl'
        self.metadata_100k_file = self.base_dir / f'meta_data_{self.PREFIX}_100k.jsonl'
        self.metadata_original_100k_file = self.base_dir / f'meta_data_{self.PREFIX}_original_100k.jsonl'

        self.exported_count = 0
        self.current_split_index = 0
        self.current_sample_index = 0
        self.metadata_stream = None
        self.metadata_original_stream = None

        self.stats = {
            'total_processed': 0,
            'total_exported': 0,
            'skipped_missing_image': 0,
            'skipped_missing_code': 0,
            'skipped_wrong_category': 0,
            'skipped_errors': 0,
            'splits_processed': {},
            'start_time': None,
        }

        self._original_sigint = None
        self._original_sigterm = None

    def _setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, saving checkpoint...")
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

    def _load_dataset_split(self, split: str, start_idx: int = 0) -> Iterator[Dict[str, Any]]:
        if self.json_data_path and self.json_data_path.exists():
            logger.info(f"Loading data from {self.json_data_path}...")
            with open(self.json_data_path, 'r') as f:
                data = json.load(f)
            logger.info(f"Loaded {len(data):,} total samples from JSON file")

            html_data = [s for s in data if self._is_html_category(s.get('category'))]
            total = len(html_data)
            logger.info(f"Filtered to {total:,} HTML samples")
            if start_idx > 0:
                if start_idx >= total:
                    logger.warning(f"start_idx {start_idx:,} >= total {total:,}; nothing to resume")
                    return iter(())
                logger.info(f"Resuming at sample {start_idx:,} (slice skip, no re-iteration)")
                html_data = html_data[start_idx:]
            return iter(html_data)

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

    def _get_human_prompt(self) -> str:
        return self.HUMAN_PROMPT

    def _init_metadata_streams(self, append: bool = False) -> None:
        """Open both JSONL metadata streams (fixed + original prompt variants)."""
        for metadata_file, stream_attr in [
            (self.metadata_file, 'metadata_stream'),
            (self.metadata_original_file, 'metadata_original_stream'),
        ]:
            mode = 'a' if (append and metadata_file.exists()) else 'w'
            stream = open(metadata_file, mode, encoding='utf-8')
            setattr(self, stream_attr, stream)

    def _append_metadata_entries(
        self,
        entry_fixed: Dict[str, Any],
        entry_original: Dict[str, Any]
    ) -> None:
        """Append a line to each JSONL stream."""
        if self.metadata_stream:
            self.metadata_stream.write(json.dumps(entry_fixed, ensure_ascii=False) + '\n')
            self.metadata_stream.flush()

        if self.metadata_original_stream:
            self.metadata_original_stream.write(json.dumps(entry_original, ensure_ascii=False) + '\n')
            self.metadata_original_stream.flush()

    def _close_metadata_streams(self) -> None:
        for stream_attr in ['metadata_stream', 'metadata_original_stream']:
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

    def _extract_html_from_messages(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        if not messages:
            return None

        for msg in messages:
            if msg.get('role') == 'assistant':
                content = msg.get('content')
                if content and isinstance(content, str) and content.strip():
                    return content.strip()

        return None

    def _extract_human_prompt_from_messages(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        if not messages:
            return None

        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content')
                if content and isinstance(content, str):
                    return content.strip()

        return None

    def _is_html_category(self, category: Any) -> bool:
        if not category:
            return False

        if isinstance(category, str):
            return category.lower() == self.HTML_CATEGORY

        return False

    def _process_sample(
        self,
        sample: Dict[str, Any],
        sample_idx: int,
    ) -> bool:
        self.stats['total_processed'] += 1

        category = sample.get('category')
        if not self._is_html_category(category):
            self.stats['skipped_wrong_category'] += 1
            return False

        images = sample.get('images')
        if not images or not isinstance(images, list) or len(images) == 0:
            self.stats['skipped_missing_image'] += 1
            return False

        image_path_str = images[0]
        if not isinstance(image_path_str, str):
            self.stats['skipped_missing_image'] += 1
            return False

        messages = sample.get('messages', [])
        html_code = self._extract_html_from_messages(messages)

        if not html_code:
            self.stats['skipped_missing_code'] += 1
            return False

        try:
            original_human_prompt = self._extract_human_prompt_from_messages(messages)
            if not original_human_prompt:
                original_human_prompt = ""

            existing_path = self.present_indices.get(self.exported_count) if self.scan_existing_tars else None
            if existing_path is not None:
                image_ref = [existing_path]
                metadata_entry_fixed = {
                    'messages': [
                        {'role': 'user', 'content': self._get_human_prompt()},
                        {'role': 'assistant', 'content': html_code},
                    ],
                    'images': image_ref,
                }
                metadata_entry_original = {
                    'messages': [
                        {'role': 'user', 'content': original_human_prompt},
                        {'role': 'assistant', 'content': html_code},
                    ],
                    'images': image_ref,
                }
                self._append_metadata_entries(metadata_entry_fixed, metadata_entry_original)
                self.exported_count += 1
                self.stats['total_exported'] += 1
                self.stats['reused_existing'] = self.stats.get('reused_existing', 0) + 1
                return True

            if image_path_str.startswith("mcd_images/"):
                zip_image_path = image_path_str
            else:
                zip_image_path = f"mcd_images/{image_path_str}"

            image_bytes = self._load_image_from_zip(zip_image_path)
            if image_bytes is None:
                self.stats['skipped_missing_image'] += 1
                return False

            file_id = self._format_file_id(self.exported_count)
            image_ref = [self.tar_writer.add_image(file_id, image_bytes, self.exported_count)]

            metadata_entry_fixed = {
                'messages': [
                    {'role': 'user', 'content': self._get_human_prompt()},
                    {'role': 'assistant', 'content': html_code},
                ],
                'images': image_ref,
            }

            metadata_entry_original = {
                'messages': [
                    {'role': 'user', 'content': original_human_prompt},
                    {'role': 'assistant', 'content': html_code},
                ],
                'images': image_ref,
            }

            self._append_metadata_entries(metadata_entry_fixed, metadata_entry_original)

            self.exported_count += 1
            self.stats['total_exported'] += 1
            return True

        except Exception as e:
            logger.debug(f"Error processing sample {sample_idx}: {e}")
            self.stats['skipped_errors'] += 1
            return False

    def _create_100k_subsets(self) -> None:
        """Create 100k subset JSONL files for both fixed and original versions."""
        for source_file, subset_file, label in [
            (self.metadata_file, self.metadata_100k_file, "fixed"),
            (self.metadata_original_file, self.metadata_original_100k_file, "original"),
        ]:
            if self.exported_count <= 100000:
                if source_file.exists():
                    import shutil
                    shutil.copy(source_file, subset_file)
                    logger.info(f"Created 100k subset file ({label}): {subset_file}")
            else:
                logger.info(f"Creating 100k subset metadata file ({label})...")
                try:
                    count = 0
                    with open(source_file, 'r', encoding='utf-8') as src, \
                         open(subset_file, 'w', encoding='utf-8') as dst:
                        for line in src:
                            if count >= 100000:
                                break
                            dst.write(line)
                            count += 1
                    logger.info(f"Created 100k subset ({label}) with {count} entries")
                except Exception as e:
                    logger.warning(f"Failed to create 100k subset ({label}): {e}")

    def _print_summary(self) -> None:
        elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
        rate = self.stats['total_exported'] / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 60)
        print("MCD Export Complete (MS-Swift Format)")
        print("=" * 60)
        print(f"Total processed: {self.stats['total_processed']:,}")
        print(f"Total exported:  {self.stats['total_exported']:,}")
        print(f"Skipped (wrong category): {self.stats['skipped_wrong_category']:,}")
        print(f"Skipped (no image):       {self.stats['skipped_missing_image']:,}")
        print(f"Skipped (no code):        {self.stats['skipped_missing_code']:,}")
        print(f"Skipped (errors):         {self.stats['skipped_errors']:,}")
        print(f"Processing rate: {rate:.2f} samples/sec")
        print(f"Elapsed time: {elapsed:.1f}s")
        print(f"\nOutput files:")
        print(f"  Tar shards: {self.base_dir}")
        print(f"  Metadata (fixed): {self.metadata_file}")
        print(f"  Metadata (original): {self.metadata_original_file}")
        if self.metadata_100k_file.exists():
            print(f"  100k Subset (fixed): {self.metadata_100k_file}")
        if self.metadata_original_100k_file.exists():
            print(f"  100k Subset (original): {self.metadata_original_100k_file}")
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

        self._open_zip()

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

        logger.info(f"MCD Dataset Fetcher (MS-Swift Format)")
        logger.info(f"  Dataset: {self.DATASET_ID}")
        logger.info(f"  Output: {self.base_dir}")
        logger.info(f"  Splits: {self.splits}")
        logger.info(f"  Max samples: {max_samples or 'all'}")
        logger.info(f"  Streaming: {self.streaming}")
        logger.info(f"  Category filter: HTML only")
        logger.info(f"  Scan existing tars: {self.scan_existing_tars}")

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
                    pbar.set_postfix({
                        'exported': self.exported_count,
                        'reused': self.stats.get('reused_existing', 0),
                        'html': self.stats['total_exported'],
                        'skipped_cat': self.stats['skipped_wrong_category'],
                    })

                    if sample_idx > 0 and sample_idx % self.checkpoint_interval == 0:
                        self._save_checkpoint()
                        gc.collect()

                    sample_idx += 1

                pbar.close()
                self.stats['splits_processed'][split] = sample_idx

                self.current_sample_index = 0
                is_resuming = False

                if max_samples and self.exported_count >= max_samples:
                    break

            self._close_zip()
            self.tar_writer.close()
            self._close_metadata_streams()
            self._save_checkpoint()

            self._create_100k_subsets()

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

    def verify_output(self) -> Dict[str, Any]:
        stats = {
            'total_images': 0,
            'metadata_entries': 0,
            'metadata_original_entries': 0,
            'metadata_100k_entries': 0,
            'metadata_original_100k_entries': 0,
            'metadata_valid': False,
            'metadata_original_valid': False,
            'metadata_100k_valid': False,
            'metadata_original_100k_valid': False,
            'mismatched_files': [],
            'sample_entries': [],
            'sample_entries_original': [],
        }

        if self.base_dir.exists():
            stats['total_images'] = count_tar_png_members(self.base_dir)

        if self.metadata_file.exists():
            valid, count, samples = _count_jsonl(self.metadata_file, sample_n=3)
            stats['metadata_entries'] = count if valid else -1
            stats['metadata_valid'] = valid
            stats['sample_entries'] = samples

        if self.metadata_original_file.exists():
            valid, count, samples = _count_jsonl(self.metadata_original_file, sample_n=3)
            stats['metadata_original_entries'] = count if valid else -1
            stats['metadata_original_valid'] = valid
            stats['sample_entries_original'] = samples

        if self.metadata_100k_file.exists():
            valid, count, _ = _count_jsonl(self.metadata_100k_file, sample_n=0)
            stats['metadata_100k_entries'] = count if valid else -1
            stats['metadata_100k_valid'] = valid

        if self.metadata_original_100k_file.exists():
            valid, count, _ = _count_jsonl(self.metadata_original_100k_file, sample_n=0)
            stats['metadata_original_100k_entries'] = count if valid else -1
            stats['metadata_original_100k_valid'] = valid

        if stats['metadata_valid'] and stats['metadata_entries'] != stats['total_images']:
            stats['mismatched_files'].append(
                f"Metadata entries ({stats['metadata_entries']}) != Image count ({stats['total_images']})"
            )

        if stats['metadata_original_valid'] and stats['metadata_original_entries'] != stats['total_images']:
            stats['mismatched_files'].append(
                f"Original metadata entries ({stats['metadata_original_entries']}) != Image count ({stats['total_images']})"
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
        description="Fetch MCD (MultimodalCodingDataset) HTML samples and export to MS-Swift JSONL format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all HTML samples from all splits
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip

  # Export first 100,000 HTML samples
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip -n 100000

  # Export to custom category directory (e.g., output/ms_swift/web2code/)
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --category-name web2code

  # Export only train split
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --splits train

  # Resume interrupted processing
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --resume

  # Verify existing output
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --verify

  # Load from local JSON file instead of HuggingFace
  python mcd_fetcher.py -o ./output --images-zip ./mcd_images.zip --json-data ./mcd_598k.json -n 200

Output structure:
  {output}/ms_swift/{category-name}/  (default: McD100k)
      images-00000.tar, images-00001.tar, ...
      meta_data_web.jsonl
      meta_data_web_original.jsonl
      meta_data_web_100k.jsonl
      meta_data_web_original_100k.jsonl

Note: Only HTML-category samples are exported. Samples from other categories
(Chart-to-Code, Image-Augmented QA, Algorithmic Problems) are skipped.
"""
    )

    parser.add_argument("-o", "--output-dir", type=str, required=True,
                        help="Base output directory (required)")
    parser.add_argument("--images-zip", type=str, required=True,
                        help="Path to mcd_images.zip file (required)")
    parser.add_argument("--json-data", type=str, default=None,
                        help="Path to MCD JSON file (e.g., mcd_598k.json) for full dataset access")
    parser.add_argument("-n", "--num-samples", type=int, default=None,
                        help="Maximum number of samples to export (default: all)")
    parser.add_argument("--category-name", type=str, default=None,
                        help="Output category name (default: McD100k). Sets output path to output/ms_swift/{category-name}/")
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

    fetcher = MCDFetcher(
        output_dir=args.output_dir,
        images_zip_path=args.images_zip,
        json_data_path=args.json_data,
        category_name=args.category_name,
        splits=args.splits,
        batch_size=args.batch_size,
        checkpoint_interval=args.checkpoint_interval,
        memory_limit_gb=args.memory_limit,
        hf_token=args.hf_token,
        streaming=not args.no_streaming,
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
