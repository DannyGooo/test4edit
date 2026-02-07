#!/usr/bin/env python3
"""
Transform webdataset tar files to Qwen3-VL training format.

This script processes webdataset tar files containing image-HTML pairs and converts them
to the JSON format expected by Qwen3-VL training scripts.

Output format:
[
  {
    "id": "chunk_X_row_Y",
    "image": "images/chunk_X_row_Y.png",
    "conversations": [
      {"from": "human", "value": "<image>\nWhat is in this image?"},
      {"from": "gpt", "value": "<HTML content>"}
    ]
  },
  ...
]
"""

import os
try:
    import ujson as json  # type: ignore
except ModuleNotFoundError:
    import json
import argparse
import glob
from pathlib import Path
from typing import List, Dict, Any
from tqdm import tqdm
import webdataset as wds
from PIL import Image
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import traceback


def extract_id_from_key(key: str) -> str:
    """
    Extract a clean ID from the webdataset key.

    Args:
        key: Webdataset key (e.g., "chunk_0_row_123")

    Returns:
        Clean ID string
    """
    return key


def create_conversation(html_content: str) -> List[Dict[str, str]]:
    """
    Create a conversation format for the dataset.

    Args:
        html_content: The complete HTML content from the webdataset

    Returns:
        List of conversation turns in the expected format
    """
    return [
        {
            "from": "human",
            "value": """<image>\nYou are an expert web developer who specializes in HTML and CSS. Given a screenshot of a reference webpage, build a pixel-perfect single-page app using only HTML and CSS.

- Make sure the app looks exactly like the screenshot.
- Pay close attention to background color, text color, font size, font family, padding, margin, border, etc. Match the colors, layouts, and sizes exactly.
- Use the exact text from the screenshot.
- Do not add comments in the code such as "<!-- Add other navigation links as needed -->" and "<!-- ... other news items ... -->" in place of writing the full code. WRITE THE FULL CODE.
- Repeat elements as needed to match the screenshot. For example, if there are 15 items, the code should have 15 items. DO NOT LEAVE comments like "<!-- Repeat for each news item -->" or bad things will happen.
- For images, use placeholder images from https://placehold.co like https://placehold.co/300x200 so that the placeholder can replaced with the image later.

Deliver only the file contents (HTML with embedded <style>).
Do not include markdown "```" or "```html" at the start or end."""
        },
        {
            "from": "gpt",
            "value": html_content
        }
    ]


def save_image_worker(image, image_path: str, sample_id: str) -> tuple:
    """
    Worker function to save an image to disk (used in thread pool).

    Args:
        image: PIL Image object or bytes
        image_path: Path where to save the image
        sample_id: Sample identifier for error reporting

    Returns:
        Tuple of (success: bool, sample_id: str, error_msg: str or None)
    """
    try:
        if isinstance(image, Image.Image):
            image.save(image_path)
        else:
            # If image is bytes, convert to PIL Image first
            img = Image.open(io.BytesIO(image))
            img.save(image_path)
        return (True, sample_id, None)
    except Exception as e:
        return (False, sample_id, str(e))


def process_webdataset(
    input_pattern: str,
    output_json_path: str,
    output_image_dir: str,
    max_samples: int = None,
    batch_size: int = 100,
    num_workers: int = 8
) -> int:
    """
    Process webdataset tar files and convert to Qwen3-VL format.

    Args:
        input_pattern: Glob pattern for input tar files (e.g., "/path/to/webdataset_chunk_*.tar")
        output_json_path: Path to output JSON file
        output_image_dir: Directory to save extracted images
        max_samples: Maximum number of samples to process (None for all)
        batch_size: Number of samples to process in each batch (for progress tracking)
        num_workers: Number of parallel workers for image saving

    Returns:
        Number of samples processed
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_image_dir, exist_ok=True)

    # Get all matching tar files and sort them
    tar_files = sorted(glob.glob(input_pattern))

    if not tar_files:
        raise ValueError(f"No tar files found matching pattern: {input_pattern}")

    print(f"Found {len(tar_files)} tar files to process")
    print(f"Output images directory: {output_image_dir}")
    print(f"Output JSON file: {output_json_path}")
    print(f"Batch size: {batch_size}")
    print(f"Parallel workers: {num_workers}")

    # Create webdataset pipeline.
    #
    # Important: webdataset will stop iteration on certain shard/sample errors unless a handler is set.
    # Using warn_and_continue lets us skip bad samples/shards and continue through all tar files.
    #
    # Note: We intentionally do NOT decode images to PIL here. Most runs are I/O bound and we
    # may skip saving images that already exist on disk; keeping `png` as bytes avoids wasted decode work.
    dataset = wds.WebDataset(tar_files, shardshuffle=False, handler=wds.warn_and_continue).to_tuple(
        "html", "png", "__key__", handler=wds.warn_and_continue
    )

    # Statistics tracking
    submitted_count = 0
    errors = []
    dataset_errors = 0
    skipped_existing_images = 0

    # Lock for thread-safe JSON writing
    write_lock = Lock()

    print("\nProcessing samples...")

    # Open JSON file for streaming output (JSONL format for efficiency)
    # We'll write one JSON object per line, then convert to array format at the end
    temp_json_path = output_json_path + ".tmp"

    with open(temp_json_path, 'w', encoding='utf-8') as json_file:
        # Use ThreadPoolExecutor for parallel image saving
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}

            try:
                for html_content, image, key in tqdm(dataset, desc="Processing"):
                    # Stop if max_samples is reached
                    if max_samples and submitted_count >= max_samples:
                        break

                    # Extract sample ID
                    sample_id = extract_id_from_key(key)

                    # Define image filename
                    image_filename = f"{sample_id}.png"
                    image_path = os.path.join(output_image_dir, image_filename)

                    # Decode HTML content if it's bytes
                    if isinstance(html_content, bytes):
                        html_content = html_content.decode('utf-8', errors='replace')

                    # If the image already exists, do not re-export it.
                    # Still emit the JSON sample pointing at the existing file.
                    if os.path.isfile(image_path) and os.path.getsize(image_path) > 0:
                        skipped_existing_images += 1
                        json_entry = {
                            "id": sample_id,
                            "image": f"images/{image_filename}",
                            "conversations": create_conversation(html_content),
                        }
                        with write_lock:
                            json_file.write(json.dumps(json_entry, ensure_ascii=False) + '\n')
                    else:
                        # Submit image save task to thread pool
                        future = executor.submit(save_image_worker, image, image_path, sample_id)

                        # Store metadata for this future
                        futures[future] = {
                            "sample_id": sample_id,
                            "image_filename": image_filename,
                            "html_content": html_content
                        }

                    submitted_count += 1

                    # Keep the futures backlog bounded. When we have enough queued work,
                    # block until at least one completes, then drain all currently-done ones.
                    if len(futures) >= batch_size:
                        _process_completed_futures(
                            futures, json_file, write_lock, errors, wait_all=False, wait_for_one=True
                        )

                # Process any remaining futures
                if futures:
                    _process_completed_futures(futures, json_file, write_lock, errors, wait_all=True)

            except KeyboardInterrupt:
                print("\nInterrupted by user (KeyboardInterrupt). Finalizing completed items...")
                if futures:
                    _process_completed_futures(futures, json_file, write_lock, errors, wait_all=True)
                raise
            except Exception as e:
                dataset_errors += 1
                print("\nFatal error during dataset iteration.")
                print(f"  Exception: {type(e).__name__}: {e!r}")
                print(f"  Submitted {submitted_count} samples before error")
                print("  Traceback:")
                print(traceback.format_exc())
                raise

    # Convert JSONL temp file to JSON array format
    print(f"\nConverting to JSON array format...")
    json_data = []
    with open(temp_json_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                json_data.append(json.loads(line))

    # Write final JSON file (compact format for efficiency)
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False)

    # Remove temporary file
    os.remove(temp_json_path)

    # Print summary
    sample_count = len(json_data)
    error_count = len(errors)
    print(f"\n✓ Transformation complete!")
    print(f"  - Successfully processed: {sample_count} samples")
    print(f"  - Failed samples: {error_count}")
    if dataset_errors:
        print(f"  - Dataset iteration errors: {dataset_errors}")
    print(f"  - Total submitted: {submitted_count}")
    print(f"  - Images already existed (skipped export): {skipped_existing_images}")
    print(f"  - Images exported this run: {sample_count - skipped_existing_images}")
    print(f"  - Images saved to: {output_image_dir}")
    print(f"  - JSON saved to: {output_json_path}")

    if errors and error_count <= 10:
        print(f"\nErrors encountered:")
        for error in errors[:10]:
            print(f"  - {error}")
    elif error_count > 10:
        print(f"\nShowing first 10 errors (total: {error_count}):")
        for error in errors[:10]:
            print(f"  - {error}")

    return sample_count


def _process_completed_futures(
    futures: dict,
    json_file,
    write_lock: Lock,
    errors: list,
    wait_all: bool = False,
    wait_for_one: bool = False,
):
    """
    Process completed futures from the thread pool.

    Args:
        futures: Dictionary mapping futures to their metadata
        json_file: File handle for JSON output
        write_lock: Lock for thread-safe writing
        errors: List to accumulate errors
        wait_all: Whether to wait for all futures to complete
    """
    completed_futures = []

    if wait_all:
        # Wait for all remaining futures
        for future in as_completed(list(futures.keys())):
            completed_futures.append(future)
    else:
        # Optionally wait for at least one completion to avoid unbounded growth.
        if wait_for_one and futures:
            completed_futures.append(next(as_completed(list(futures.keys()))))

        # Drain any other already-completed futures
        for future in list(futures.keys()):
            if future.done() and future not in completed_futures:
                completed_futures.append(future)

    for future in completed_futures:
        metadata = futures[future]
        success, sample_id, error_msg = future.result()

        if success:
            # Create JSON entry
            json_entry = {
                "id": metadata["sample_id"],
                "image": f"images/{metadata['image_filename']}",
                "conversations": create_conversation(metadata["html_content"])
            }

            # Write to file (thread-safe)
            with write_lock:
                json_file.write(json.dumps(json_entry, ensure_ascii=False) + '\n')
        else:
            errors.append(f"Sample {sample_id}: {error_msg}")

        # Remove processed future
        del futures[future]


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Transform webdataset tar files to Qwen3-VL training format"
    )
    parser.add_argument(
        "--input-pattern",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/coco_image/webdataset_chunk_*.tar",
        help="Glob pattern for input webdataset tar files"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json",
        help="Path to output JSON file"
    )
    parser.add_argument(
        "--output-image-dir",
        type=str,
        default="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/images",
        help="Directory to save extracted images"
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Maximum number of samples to process (default: all)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of samples to process in each batch (default: 100)"
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=8,
        help="Number of parallel workers for image saving (default: 8)"
    )

    args = parser.parse_args()

    # Process the dataset
    process_webdataset(
        input_pattern=args.input_pattern,
        output_json_path=args.output_json,
        output_image_dir=args.output_image_dir,
        max_samples=args.max_samples,
        batch_size=args.batch_size,
        num_workers=args.num_workers
    )


if __name__ == "__main__":
    main()
