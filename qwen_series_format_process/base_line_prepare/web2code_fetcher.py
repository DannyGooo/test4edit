#!/usr/bin/env python3
"""
Web2Code Dataset Fetcher for Qwen Series format.

Fetches the MBZUAI/Web2Code dataset from HuggingFace and exports
to Qwen Series format for training vision-language models.

Filters:
    - Only keeps samples where GPT response starts with <html> or <!DOCTYPE html>
    - Only keeps samples where GPT response ends with </html>
    - Only keeps single-round conversations (1 human + 1 gpt)

Output structure:
    output/qwen_series/web2code/
        images/
            web2code_000000.png, web2code_000001.png, ...
        meta_data_web2code.json         # Original prompts from dataset
        meta_data_web2code_fixed.json   # Fixed prompt template
        meta_data_web2code_100k.json    # 100k subset (original prompts)
        meta_data_web2code_fixed_100k.json  # 100k subset (fixed prompts)

Setup:
    1. Download images: wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code_image.zip
    2. Download data: wget https://huggingface.co/datasets/MBZUAI/Web2Code/resolve/main/Web2Code.json

Usage:
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --json-data ./Web2Code.json -n 100
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --resume
    python web2code_fetcher.py -o ./output --images-zip ./Web2Code_image.zip --verify

Note:
    The HuggingFace load_dataset() only returns ~100 sample preview.
    For the full dataset (800k+ samples), download and use Web2Code.json with --json-data.
"""

import argparse
import gc
import io
import json
import logging
import os
import re
import signal
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Web2CodeFetcher:
    """Fetches Web2Code dataset and exports to Qwen Series format."""

    # Class constants
    DATASET_ID = "MBZUAI/Web2Code"
    PREFIX = "web2code"
    CATEGORY_NUM = "web2code"

    # Fixed human prompt template for web development (same as web2m_fetcher)
    HUMAN_PROMPT = """<image>
You are an expert web developer who specializes in HTML and CSS. Given a screenshot of a reference webpage, build a pixel-perfect single-page app using only HTML and CSS.

- Make sure the app looks exactly like the screenshot.
- Pay close attention to background color, text color, font size, font family, padding, margin, border, etc. Match the colors, layouts, and sizes exactly.
- Use the exact text from the screenshot.
- Do not add comments in the code such as "<!-- Add other navigation links as needed -->" and "<!-- ... other news items ... -->" in place of writing the full code. WRITE THE FULL CODE.
- Repeat elements as needed to match the screenshot. For example, if there are 15 items, the code should have 15 items. DO NOT LEAVE comments like "<!-- Repeat for each news item -->" or bad things will happen.
- For images, use placeholder images from https://placehold.co like https://placehold.co/300x200 so that the placeholder can replaced with the image later.

Deliver only the file contents (HTML with embedded <style>).
Do not include markdown "```" or "```html" at the start or end."""

    # Regex patterns for HTML validation
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
    ):
        """
        Initialize the Web2Code fetcher.

        Args:
            output_dir: Base output directory (e.g., './output')
            images_zip_path: Path to Web2Code_image.zip file
            json_data_path: Path to Web2Code.json file (for full dataset access)
            splits: Dataset splits to process (default: ['train'])
            batch_size: Number of samples to process before progress update
            checkpoint_interval: Samples between checkpoint saves
            memory_limit_gb: Memory limit in GB
            hf_token: HuggingFace token (optional)
            streaming: Use streaming mode for large dataset
        """
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

        # Setup directory structure for qwen_series format
        self.base_dir = self.output_dir / 'qwen_series' / self.CATEGORY_NUM
        self.images_dir = self.base_dir / 'images'

        # Checkpoint and metadata files
        self.checkpoint_file = self.base_dir / '.web2code_checkpoint.json'
        self.metadata_file = self.base_dir / f'meta_data_{self.PREFIX}.json'
        self.metadata_fixed_file = self.base_dir / f'meta_data_{self.PREFIX}_fixed.json'
        self.metadata_100k_file = self.base_dir / f'meta_data_{self.PREFIX}_100k.json'
        self.metadata_fixed_100k_file = self.base_dir / f'meta_data_{self.PREFIX}_fixed_100k.json'

        # State tracking
        self.exported_count = 0
        self.current_split_index = 0
        self.current_sample_index = 0
        self.metadata_stream = None
        self.metadata_fixed_stream = None
        self.is_first_entry = True

        # Statistics
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

        # Signal handlers will be set up during run()
        self._original_sigint = None
        self._original_sigterm = None

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown handlers."""
        def handler(signum, frame):
            logger.info(f"\nReceived signal {signum}, saving checkpoint...")
            self._close_zip()
            self._close_metadata_streams()
            self._save_checkpoint()
            # Restore original handlers before exit
            self._restore_signal_handlers()
            sys.exit(0)

        self._original_sigint = signal.signal(signal.SIGINT, handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, handler)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)

    def _open_zip(self) -> None:
        """Open the ZIP file for reading."""
        self.zip_file = zipfile.ZipFile(self.images_zip_path, 'r')
        logger.info(f"Opened ZIP file: {self.images_zip_path}")

    def _close_zip(self) -> None:
        """Close the ZIP file."""
        if self.zip_file:
            self.zip_file.close()
            self.zip_file = None

    def _setup_directories(self) -> None:
        """Create output directory structure."""
        for directory in [self.base_dir, self.images_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output directories created at {self.base_dir}")

    def _load_checkpoint(self) -> bool:
        """Load checkpoint for resume capability."""
        if not self.checkpoint_file.exists():
            return False

        try:
            with open(self.checkpoint_file, 'r') as f:
                checkpoint = json.load(f)

            self.exported_count = checkpoint.get('exported_count', 0)
            self.current_split_index = checkpoint.get('current_split_index', 0)
            self.current_sample_index = checkpoint.get('current_sample_index', 0)
            self.stats = checkpoint.get('stats', self.stats)
            self.is_first_entry = False  # Resuming means entries exist

            logger.info(f"Resumed from checkpoint:")
            logger.info(f"  Exported count: {self.exported_count}")
            logger.info(f"  Current split: {self.splits[self.current_split_index] if self.current_split_index < len(self.splits) else 'done'}")
            logger.info(f"  Sample index: {self.current_sample_index}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return False

    def _save_checkpoint(self) -> None:
        """Save current progress to checkpoint file."""
        checkpoint = {
            'exported_count': self.exported_count,
            'current_split_index': self.current_split_index,
            'current_sample_index': self.current_sample_index,
            'stats': self.stats,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }

        # Ensure directory exists
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)

    def _load_dataset_split(self, split: str) -> Iterator[Dict[str, Any]]:
        """Load a specific split of the dataset."""
        # Use JSON file if provided (recommended for full dataset access)
        if self.json_data_path and self.json_data_path.exists():
            logger.info(f"Loading data from {self.json_data_path}...")
            with open(self.json_data_path, 'r') as f:
                data = json.load(f)
            logger.info(f"Loaded {len(data):,} samples from JSON file")
            return iter(data)

        # Fallback to HuggingFace (limited to ~100 sample preview)
        logger.warning("Using HuggingFace preview (limited to ~100 samples). "
                      "For full dataset, download Web2Code.json and use --json-data")

        from datasets import load_dataset

        logger.info(f"Loading {split} split from {self.DATASET_ID}...")

        if self.streaming:
            # Streaming mode for memory efficiency
            ds = load_dataset(
                self.DATASET_ID,
                split=split,
                streaming=True,
                token=self.hf_token,
            )
            return iter(ds)
        else:
            # Non-streaming for smaller datasets or random access
            ds = load_dataset(
                self.DATASET_ID,
                split=split,
                token=self.hf_token,
            )
            return iter(ds)

    def _process_image(self, image_obj: Any) -> Optional[bytes]:
        """Convert HuggingFace image object to PNG bytes."""
        from PIL import Image

        try:
            # Handle various image formats from HuggingFace
            if isinstance(image_obj, Image.Image):
                pil_img = image_obj
            elif isinstance(image_obj, dict):
                # Dict format: {'bytes': ..., 'path': ...}
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

            # Normalize to RGB for consistent PNG output
            if pil_img.mode in ('RGBA', 'LA', 'P'):
                # Convert with white background for transparency
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

            # Save to bytes
            buf = io.BytesIO()
            pil_img.save(buf, format='PNG')
            return buf.getvalue()

        except Exception as e:
            logger.debug(f"Image processing error: {e}")
            return None

    def _load_image_from_zip(self, image_path: str) -> Optional[bytes]:
        """Load image from ZIP file and convert to PNG bytes."""
        from PIL import Image

        try:
            # Read image bytes from ZIP
            image_data = self.zip_file.read(image_path)
            pil_img = Image.open(io.BytesIO(image_data))

            # Normalize to RGB for consistent PNG output
            if pil_img.mode in ('RGBA', 'LA', 'P'):
                # Convert with white background for transparency
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

            # Save to bytes
            buf = io.BytesIO()
            pil_img.save(buf, format='PNG')
            return buf.getvalue()

        except KeyError:
            # File not found in ZIP
            return None
        except Exception as e:
            logger.debug(f"Image loading error for {image_path}: {e}")
            return None

    def _format_file_id(self, num: int) -> str:
        """Generate zero-padded file ID."""
        return f"{self.PREFIX}_{num:06d}"

    def _is_valid_html_response(self, gpt_value: str) -> bool:
        """
        Check if GPT response is valid HTML.

        Returns True if:
        - Starts with <html> or <!DOCTYPE html> (case-insensitive)
        - Ends with </html> (case-insensitive)
        """
        if not gpt_value or not isinstance(gpt_value, str):
            return False

        has_valid_start = bool(self.HTML_START_PATTERN.match(gpt_value))
        has_valid_end = bool(self.HTML_END_PATTERN.search(gpt_value))

        return has_valid_start and has_valid_end

    def _is_single_round_conversation(self, conversations: List[Dict[str, Any]]) -> bool:
        """
        Check if conversation is a single round (1 human + 1 gpt).

        Returns True if:
        - Exactly 2 entries in conversations
        - First entry is from 'human'
        - Second entry is from 'gpt'
        """
        if not conversations or len(conversations) != 2:
            return False

        first_from = conversations[0].get('from', '').lower()
        second_from = conversations[1].get('from', '').lower()

        return first_from == 'human' and second_from == 'gpt'

    def _init_metadata_streams(self, append: bool = False) -> None:
        """Initialize streaming metadata JSON files (both original and fixed)."""
        for metadata_file, stream_attr in [
            (self.metadata_file, 'metadata_stream'),
            (self.metadata_fixed_file, 'metadata_fixed_stream'),
        ]:
            if append and metadata_file.exists():
                # Read existing content
                content = metadata_file.read_text()
                trimmed = content.rstrip()

                # Remove closing bracket to continue array
                if trimmed.endswith(']'):
                    metadata_file.write_text(trimmed[:-1])

                # Open in append mode
                stream = open(metadata_file, 'a', encoding='utf-8')
                setattr(self, stream_attr, stream)
            else:
                # Fresh start
                stream = open(metadata_file, 'w', encoding='utf-8')
                stream.write('[\n')
                setattr(self, stream_attr, stream)

        if not append:
            self.is_first_entry = True

    def _append_metadata_entries(
        self,
        entry_original: Dict[str, Any],
        entry_fixed: Dict[str, Any]
    ) -> None:
        """Append metadata entries to both streams."""
        prefix = '' if self.is_first_entry else ',\n'

        if self.metadata_stream:
            self.metadata_stream.write(prefix + json.dumps(entry_original, indent=2))

        if self.metadata_fixed_stream:
            self.metadata_fixed_stream.write(prefix + json.dumps(entry_fixed, indent=2))

        self.is_first_entry = False

    def _close_metadata_streams(self) -> None:
        """Close both metadata streams properly."""
        for stream_attr in ['metadata_stream', 'metadata_fixed_stream']:
            stream = getattr(self, stream_attr, None)
            if stream:
                stream.write('\n]\n')
                stream.close()
                setattr(self, stream_attr, None)

    def _check_memory(self) -> bool:
        """Check if memory usage is within limits."""
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
            return True  # Skip check if psutil not available

    def _process_sample(
        self,
        sample: Dict[str, Any],
        sample_idx: int,
    ) -> bool:
        """
        Process a single dataset sample.

        Returns:
            True if sample was successfully exported, False otherwise
        """
        self.stats['total_processed'] += 1

        # Extract fields - Web2Code uses 'image' (path string) and 'conversations' fields
        image_path_str = sample.get('image')
        conversations = sample.get('conversations', [])

        # Validate single-round conversation
        if not self._is_single_round_conversation(conversations):
            self.stats['skipped_multi_round'] += 1
            return False

        # Get GPT response and validate HTML structure
        gpt_value = conversations[1].get('value', '')
        if not self._is_valid_html_response(gpt_value):
            self.stats['skipped_invalid_html'] += 1
            return False

        # Validate image path
        if not image_path_str:
            self.stats['skipped_missing_image'] += 1
            return False

        try:
            # Load image from ZIP file
            # image_path_str is like "pix2code/image_id.png" or "WebSight_images_new/image_xxx.png"
            image_bytes = self._load_image_from_zip(image_path_str)
            if image_bytes is None:
                self.stats['skipped_missing_image'] += 1
                return False

            # Generate file ID
            file_id = self._format_file_id(self.exported_count)

            # Save image
            image_path = self.images_dir / f"{file_id}.png"
            with open(image_path, 'wb') as f:
                f.write(image_bytes)

            # Get original human prompt from dataset
            human_value_original = conversations[0].get('value', '')

            # Create metadata entry with original prompt
            metadata_entry_original = {
                'id': file_id,
                'image': f"images/{file_id}.png",
                'conversations': [
                    {
                        'from': 'human',
                        'value': human_value_original
                    },
                    {
                        'from': 'gpt',
                        'value': gpt_value
                    }
                ]
            }

            # Create metadata entry with fixed prompt
            metadata_entry_fixed = {
                'id': file_id,
                'image': f"images/{file_id}.png",
                'conversations': [
                    {
                        'from': 'human',
                        'value': self.HUMAN_PROMPT
                    },
                    {
                        'from': 'gpt',
                        'value': gpt_value
                    }
                ]
            }

            # Append to both metadata streams
            self._append_metadata_entries(metadata_entry_original, metadata_entry_fixed)

            self.exported_count += 1
            self.stats['total_exported'] += 1
            return True

        except Exception as e:
            logger.debug(f"Error processing sample {sample_idx}: {e}")
            self.stats['skipped_errors'] += 1
            return False

    def _create_100k_subsets(self) -> None:
        """Create 100k subset metadata files for both original and fixed versions."""
        for source_file, subset_file, label in [
            (self.metadata_file, self.metadata_100k_file, "original"),
            (self.metadata_fixed_file, self.metadata_fixed_100k_file, "fixed"),
        ]:
            if self.exported_count <= 100000:
                # If we have 100k or fewer, just copy the main file
                if source_file.exists():
                    import shutil
                    shutil.copy(source_file, subset_file)
                    logger.info(f"Created 100k subset file ({label}): {subset_file}")
                continue

            logger.info(f"Creating 100k subset metadata file ({label})...")
            try:
                # Read and truncate to first 100k entries
                with open(source_file, 'r') as f:
                    metadata = json.load(f)

                subset = metadata[:100000]
                with open(subset_file, 'w') as f:
                    json.dump(subset, f, indent=2)

                logger.info(f"Created 100k subset ({label}) with {len(subset)} entries")
            except Exception as e:
                logger.warning(f"Failed to create 100k subset ({label}): {e}")

    def _print_summary(self) -> None:
        """Print final processing summary."""
        elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
        rate = self.stats['total_exported'] / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 60)
        print("Web2Code Export Complete (Qwen Series Format)")
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
        print(f"  Images: {self.images_dir}")
        print(f"  Metadata (original): {self.metadata_file}")
        print(f"  Metadata (fixed): {self.metadata_fixed_file}")
        if self.metadata_100k_file.exists():
            print(f"  100k Subset (original): {self.metadata_100k_file}")
        if self.metadata_fixed_100k_file.exists():
            print(f"  100k Subset (fixed): {self.metadata_fixed_100k_file}")
        print("=" * 60)

    def run(
        self,
        max_samples: Optional[int] = None,
        resume: bool = True,
    ) -> Dict[str, Any]:
        """
        Main execution method.

        Args:
            max_samples: Maximum total samples to export (None for all)
            resume: Whether to resume from checkpoint

        Returns:
            Statistics dictionary
        """
        from tqdm import tqdm

        self.stats['start_time'] = time.time()

        # Force non-streaming when max_samples is specified to ensure we can access enough samples
        # Streaming mode may return samples in different order or skip samples, making it unreliable
        # for exact sample count guarantees
        effective_streaming = self.streaming and (max_samples is None)
        if max_samples and self.streaming:
            logger.info(f"Disabling streaming mode to ensure access to enough samples for -n {max_samples}")
        self.streaming = effective_streaming

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

        # Setup directories
        self._setup_directories()

        # Load checkpoint if resuming
        is_resuming = resume and self._load_checkpoint()

        # Initialize metadata streams
        self._init_metadata_streams(append=is_resuming)

        # Open ZIP file for reading images
        self._open_zip()

        logger.info(f"Web2Code Dataset Fetcher (Qwen Series Format)")
        logger.info(f"  Dataset: {self.DATASET_ID}")
        logger.info(f"  Output: {self.base_dir}")
        logger.info(f"  Splits: {self.splits}")
        logger.info(f"  Max samples: {max_samples or 'all'}")
        logger.info(f"  Streaming: {self.streaming}")
        logger.info(f"  Filters: single-round + valid HTML structure")

        try:
            # Process each split until we have enough exports
            for split_idx in range(self.current_split_index, len(self.splits)):
                split = self.splits[split_idx]
                self.current_split_index = split_idx

                logger.info(f"\nProcessing split: {split}")

                # Skip to resume point if needed
                start_idx = self.current_sample_index if split_idx == self.current_split_index and is_resuming else 0

                # Load dataset split
                dataset_iter = self._load_dataset_split(split)

                # Create progress bar
                pbar = tqdm(
                    desc=f"Processing {split}",
                    unit="samples",
                )

                sample_idx = 0
                for sample in dataset_iter:
                    # Skip samples we've already processed (for resume)
                    if sample_idx < start_idx:
                        sample_idx += 1
                        pbar.update(1)
                        continue

                    self.current_sample_index = sample_idx

                    # Check if reached export limit (check EXPORTED count, not processed)
                    if max_samples and self.exported_count >= max_samples:
                        logger.info(f"Reached export limit: {max_samples} samples exported")
                        pbar.close()
                        break

                    # Check memory periodically
                    if sample_idx % 100 == 0:
                        self._check_memory()

                    # Process sample
                    self._process_sample(sample, sample_idx)

                    # Update progress
                    pbar.update(1)
                    pbar.set_postfix({
                        'exported': self.exported_count,
                        'skipped': self.stats['total_processed'] - self.stats['total_exported'],
                    })

                    # Checkpoint
                    if sample_idx > 0 and sample_idx % self.checkpoint_interval == 0:
                        self._save_checkpoint()
                        gc.collect()

                    sample_idx += 1
                else:
                    # Loop ended without break (iterator exhausted)
                    pbar.close()
                    if max_samples and self.exported_count < max_samples:
                        logger.warning(f"Split '{split}' exhausted after {sample_idx} samples. Exported {self.exported_count}/{max_samples} samples so far.")

                self.stats['splits_processed'][split] = sample_idx

                # Reset sample index for next split
                self.current_sample_index = 0
                is_resuming = False

                # Check if reached limit
                if max_samples and self.exported_count >= max_samples:
                    break

            # Final cleanup
            self._close_zip()
            self._close_metadata_streams()
            self._save_checkpoint()

            # Create 100k subset files
            self._create_100k_subsets()

            # Remove checkpoint on success
            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()

            # Restore signal handlers before returning
            self._restore_signal_handlers()

            self._print_summary()
            return self.stats

        except KeyboardInterrupt:
            logger.info("\nInterrupted by user, saving checkpoint...")
            self._close_zip()
            self._close_metadata_streams()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

        except Exception as e:
            logger.error(f"Error during processing: {e}")
            self._close_zip()
            self._close_metadata_streams()
            self._save_checkpoint()
            self._restore_signal_handlers()
            raise

    def verify_output(self) -> Dict[str, Any]:
        """Verify integrity of existing output files."""
        stats = {
            'total_images': 0,
            'metadata_entries': 0,
            'metadata_fixed_entries': 0,
            'metadata_100k_entries': 0,
            'metadata_fixed_100k_entries': 0,
            'metadata_valid': False,
            'metadata_fixed_valid': False,
            'metadata_100k_valid': False,
            'metadata_fixed_100k_valid': False,
            'mismatched_files': [],
            'sample_entries': [],
            'html_validation': {'valid': 0, 'invalid': 0},
        }

        # Count images
        if self.images_dir.exists():
            stats['total_images'] = len(list(self.images_dir.glob('*.png')))

        # Check all metadata files
        for file_path, entries_key, valid_key in [
            (self.metadata_file, 'metadata_entries', 'metadata_valid'),
            (self.metadata_fixed_file, 'metadata_fixed_entries', 'metadata_fixed_valid'),
            (self.metadata_100k_file, 'metadata_100k_entries', 'metadata_100k_valid'),
            (self.metadata_fixed_100k_file, 'metadata_fixed_100k_entries', 'metadata_fixed_100k_valid'),
        ]:
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        metadata = json.load(f)
                    stats[entries_key] = len(metadata)
                    stats[valid_key] = True

                    # Sample first 3 entries for main metadata
                    if entries_key == 'metadata_entries' and len(metadata) >= 3:
                        stats['sample_entries'] = metadata[:3]

                    # Validate HTML in GPT responses for main metadata
                    if entries_key == 'metadata_entries':
                        for entry in metadata[:100]:  # Check first 100
                            if 'conversations' in entry and len(entry['conversations']) >= 2:
                                gpt_value = entry['conversations'][1].get('value', '')
                                if self._is_valid_html_response(gpt_value):
                                    stats['html_validation']['valid'] += 1
                                else:
                                    stats['html_validation']['invalid'] += 1
                except json.JSONDecodeError:
                    stats[entries_key] = -1
                    stats[valid_key] = False

        # Check for mismatches
        if stats['metadata_valid'] and stats['metadata_entries'] != stats['total_images']:
            stats['mismatched_files'].append(
                f"Metadata entries ({stats['metadata_entries']}) != Image count ({stats['total_images']})"
            )

        # Validate conversations structure
        if stats['metadata_valid'] and stats['sample_entries']:
            for entry in stats['sample_entries']:
                if 'conversations' not in entry:
                    stats['mismatched_files'].append(f"Entry {entry.get('id', 'unknown')} missing conversations")
                elif len(entry['conversations']) != 2:
                    stats['mismatched_files'].append(f"Entry {entry.get('id', 'unknown')} has {len(entry['conversations'])} conversations, expected 2")

        return stats


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch Web2Code dataset and export to Qwen Series format",
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
  {output}/qwen_series/web2code/
      images/web2code_000000.png, web2code_000001.png, ...
      meta_data_web2code.json         (original prompts)
      meta_data_web2code_fixed.json   (fixed prompts)
      meta_data_web2code_100k.json    (100k subset, original)
      meta_data_web2code_fixed_100k.json (100k subset, fixed)

Filters applied:
  - Only single-round conversations (1 human + 1 gpt)
  - GPT response must start with <html> or <!DOCTYPE html>
  - GPT response must end with </html>
"""
    )

    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        required=True,
        help="Base output directory (required)"
    )

    parser.add_argument(
        "--images-zip",
        type=str,
        required=True,
        help="Path to Web2Code_image.zip file (required)"
    )

    parser.add_argument(
        "--json-data",
        type=str,
        default=None,
        help="Path to Web2Code.json file (download from HuggingFace for full dataset of 800k+ samples)"
    )

    parser.add_argument(
        "-n", "--num-samples",
        type=int,
        default=None,
        help="Maximum number of samples to EXPORT (continues processing until this many pass filters)"
    )

    parser.add_argument(
        "--splits",
        type=str,
        nargs='+',
        default=['train'],
        help="Dataset splits to process (default: train)"
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for progress updates (default: 100)"
    )

    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=1000,
        help="Samples between checkpoint saves (default: 1000)"
    )

    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="Disable streaming mode (downloads entire dataset)"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint if exists"
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, ignoring existing checkpoint"
    )

    parser.add_argument(
        "--hf-token",
        type=str,
        default=None,
        help="HuggingFace API token (optional)"
    )

    parser.add_argument(
        "--memory-limit",
        type=float,
        default=50.0,
        help="Memory limit in GB (default: 50)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing output files and exit"
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine resume behavior
    resume = not args.no_resume  # Resume by default unless --no-resume
    if args.resume:
        resume = True

    # Initialize fetcher
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
    )

    if args.verify:
        # Verify mode
        stats = fetcher.verify_output()
        print(json.dumps(stats, indent=2))
        return

    # Run fetcher
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
