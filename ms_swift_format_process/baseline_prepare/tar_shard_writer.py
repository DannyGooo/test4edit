#!/usr/bin/env python3
"""Helpers for writing MS-Swift image assets into tar shards.

Also provides shared scan/repair/verify utilities used by all fetchers
to recover from interrupted runs while preserving the per-shard
"5000 sequential PNGs" invariant.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SHARD_PATH_RE = re.compile(r"images-(\d{5})\.tar$")


class TarShardWriter:
    """Write PNG assets into sequential tar shards like images-00000.tar."""

    def __init__(
        self,
        base_dir: Path,
        shard_size: int = 5000,
        tar_prefix: str = "images",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.shard_size = shard_size
        self.tar_prefix = tar_prefix
        self._current_shard_index: Optional[int] = None
        self._tar: Optional[tarfile.TarFile] = None

    def _tar_name(self, shard_index: int) -> str:
        return f"{self.tar_prefix}-{shard_index:05d}.tar"

    def _tar_path(self, shard_index: int) -> Path:
        return self.base_dir / self._tar_name(shard_index)

    def _open_shard(self, shard_index: int, append: bool) -> None:
        if self._tar is not None and self._current_shard_index == shard_index:
            return
        self.close()
        mode = "a" if append and self._tar_path(shard_index).exists() else "w"
        self._tar = tarfile.open(self._tar_path(shard_index), mode)
        self._current_shard_index = shard_index

    def add_image(self, file_id: str, image_bytes: bytes, sample_index: int) -> str:
        shard_index = sample_index // self.shard_size
        append = sample_index % self.shard_size != 0
        self._open_shard(shard_index, append=append)

        tar_member_name = f"{file_id}.png"
        info = tarfile.TarInfo(name=tar_member_name)
        info.size = len(image_bytes)
        self._tar.addfile(info, io.BytesIO(image_bytes))
        return f"{self._tar_name(shard_index)}/{tar_member_name}"

    def flush(self) -> None:
        if self._tar is None:
            return
        if self._tar.fileobj is not None and hasattr(self._tar.fileobj, "flush"):
            self._tar.fileobj.flush()

    def close(self) -> None:
        if self._tar is not None:
            self._tar.close()
            self._tar = None
            self._current_shard_index = None


def count_tar_png_members(base_dir: Path, tar_prefix: str = "images") -> int:
    """Count PNG members across all tar shards in a directory."""
    total = 0
    for tar_path in sorted(Path(base_dir).glob(f"{tar_prefix}-*.tar")):
        with tarfile.open(tar_path, "r") as tar:
            total += sum(1 for member in tar if member.isfile() and member.name.endswith(".png"))
    return total


def png_member_pattern(prefix: str) -> "re.Pattern[str]":
    return re.compile(rf"^{re.escape(prefix)}_(\d{{6}})\.png$")


def validate_shard(tar_path: Path) -> bool:
    """Return True if the tar can be fully iterated AND reopened in append mode.

    Tarballs whose stream of headers reads cleanly can still have a corrupt /
    truncated EOF marker. Such a tar reads fine but fails when reopened in
    `'a'` mode with `tarfile.ReadError: empty header` — which is exactly the
    error that has been silently swallowed in resume loops. We must test both
    modes to surface the latter.
    """
    try:
        with tarfile.open(tar_path, "r") as tar:
            for _ in tar:
                pass
    except Exception as e:
        logger.warning(f"{tar_path.name} failed read validation: {e}")
        return False
    try:
        tarfile.open(tar_path, "a").close()
    except Exception as e:
        logger.warning(f"{tar_path.name} failed append validation: {e}")
        return False
    return True


def repair_shard(tar_path: Path, prefix: str, shard_size: int = 5000) -> int:
    """Extract every readable PNG member and rewrite the tar in sorted order.

    Returns the number of members kept.
    """
    member_pat = png_member_pattern(prefix)
    shard_match = _SHARD_PATH_RE.search(tar_path.name)
    if not shard_match:
        return 0
    shard_idx = int(shard_match.group(1))
    expected_lo = shard_idx * shard_size
    expected_hi = expected_lo + shard_size

    members: Dict[int, bytes] = {}
    try:
        with tarfile.open(tar_path, "r") as tar:
            while True:
                try:
                    member = tar.next()
                except Exception as e:
                    logger.warning(f"Stopped reading {tar_path.name} at corrupt block: {e}")
                    break
                if member is None:
                    break
                if not member.isfile():
                    continue
                m = member_pat.match(member.name)
                if not m:
                    continue
                idx = int(m.group(1))
                if not (expected_lo <= idx < expected_hi):
                    logger.warning(
                        f"  member {member.name} out of expected range "
                        f"[{expected_lo}, {expected_hi}); keeping anyway"
                    )
                try:
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    members[idx] = f.read()
                except Exception as e:
                    logger.warning(f"  unreadable member {member.name}: {e}")
    except Exception as e:
        logger.warning(f"Could not open {tar_path.name} for repair: {e}")

    tmp_path = tar_path.with_suffix(".tar.repair-tmp")
    with tarfile.open(tmp_path, "w") as new_tar:
        for idx in sorted(members):
            name = f"{prefix}_{idx:06d}.png"
            data = members[idx]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            new_tar.addfile(info, io.BytesIO(data))
    tmp_path.replace(tar_path)
    logger.info(
        f"Repaired {tar_path.name}: kept {len(members):,} members "
        f"(shard range [{expected_lo}, {expected_hi}))"
    )
    return len(members)


def validate_and_repair_shards(base_dir: Path, prefix: str, shard_size: int = 5000) -> None:
    """Run validate + repair on every images-*.tar in base_dir."""
    for tar_path in sorted(Path(base_dir).glob("images-*.tar")):
        if not validate_shard(tar_path):
            logger.info(f"Repairing {tar_path.name}...")
            repair_shard(tar_path, prefix, shard_size=shard_size)


def scan_present_indices(base_dir: Path, prefix: str) -> Dict[int, str]:
    """Return mapping {exported_count: tar_member_path} for every PNG in shards."""
    pattern = png_member_pattern(prefix)
    present: Dict[int, str] = {}
    for tar_path in sorted(Path(base_dir).glob("images-*.tar")):
        try:
            with tarfile.open(tar_path, "r") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    m = pattern.match(member.name)
                    if m:
                        idx = int(m.group(1))
                        present[idx] = f"{tar_path.name}/{member.name}"
        except Exception as e:
            logger.warning(f"Failed to scan {tar_path.name}: {e}")
    return present


def verify_shard_sequence(
    base_dir: Path,
    prefix: str,
    shard_size: int = 5000,
) -> List[str]:
    """Check each tar has shard_size members in sequence (last shard may be partial).

    Returns a list of human-readable issues; empty list means all shards conform.
    """
    member_pat = png_member_pattern(prefix)
    tar_paths = sorted(Path(base_dir).glob("images-*.tar"))
    issues: List[str] = []
    for i, tar_path in enumerate(tar_paths):
        shard_match = _SHARD_PATH_RE.search(tar_path.name)
        if not shard_match:
            issues.append(f"{tar_path.name}: filename does not match expected pattern")
            continue
        shard_idx = int(shard_match.group(1))
        expected_lo = shard_idx * shard_size
        try:
            with tarfile.open(tar_path, "r") as tar:
                indices: List[int] = []
                for member in tar:
                    if not member.isfile():
                        continue
                    m = member_pat.match(member.name)
                    if m:
                        indices.append(int(m.group(1)))
        except Exception as e:
            issues.append(f"{tar_path.name}: cannot read ({e})")
            continue

        is_last = (i == len(tar_paths) - 1)
        count = len(indices)
        if not is_last and count != shard_size:
            issues.append(
                f"{tar_path.name}: has {count} members, expected {shard_size}"
            )
        if is_last and count > shard_size:
            issues.append(
                f"{tar_path.name}: last shard has {count} members, exceeds {shard_size}"
            )

        expected_seq = list(range(expected_lo, expected_lo + count))
        if indices != expected_seq:
            missing = sorted(set(expected_seq) - set(indices))[:5]
            first_disorder = next(
                (j for j, (a, b) in enumerate(zip(indices, expected_seq)) if a != b),
                None,
            )
            issues.append(
                f"{tar_path.name}: not sequential "
                f"(first divergence at position {first_disorder}, missing examples: {missing})"
            )
    return issues
