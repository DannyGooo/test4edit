#!/usr/bin/env python3
"""
Deduplicate screenshot-to-HTML samples with:
1) HTML near-duplicate clustering via SimHash on visible text + DOM tag sequence
2) Optional screenshot near-duplicate clustering via pHash/aHash/dHash

Selection policy:
- Keep top-k entries per HTML cluster by quality score
- Optionally apply image dedup on HTML survivors and keep top-k per image cluster
- Optionally cap final output to top-N by quality score
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
from dataclasses import dataclass
from hashlib import blake2b
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


SKIP_TEXT_TAGS = {"script", "style", "noscript", "template"}
VALUE_ATTRS = {"value", "placeholder", "alt", "title", "aria-label", "content", "href", "src"}
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
CHUNK_MEMBER_RE = re.compile(r"^(chunk_\d+)_row_\d+\.[A-Za-z0-9]+$")


@dataclass
class Sample:
    sample_pos: int
    index: int
    entry: dict
    sample_id: str
    html: str
    quality: float
    text_hash: int
    dom_hash: int
    total_tags: int
    value_tags: int
    image_ref: Optional[str]
    image_path: Optional[str]
    image_hash: Optional[int] = None


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


class VisibleTextDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: List[str] = []
        self._text_chunks: List[str] = []
        self._dom_tags: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        self._stack.append(t)
        self._dom_tags.append(t)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._dom_tags.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if not self._stack:
            return
        while self._stack:
            x = self._stack.pop()
            if x == t:
                break

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        if any(t in SKIP_TEXT_TAGS for t in self._stack):
            return
        text = (data or "").strip()
        if text:
            self._text_chunks.append(text)

    @property
    def visible_text(self) -> str:
        return " ".join(self._text_chunks)

    @property
    def dom_sequence(self) -> str:
        return " ".join(self._dom_tags)


@dataclass
class _TagInfo:
    name: str
    has_direct_text: bool = False
    has_value_attr: bool = False


class ValueDensityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.total_tags: int = 0
        self._stack: List[int] = []
        self._tags: Dict[int, _TagInfo] = {}
        self._next_id: int = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._on_start_tag(tag, attrs, is_self_closing=False)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._on_start_tag(tag, attrs, is_self_closing=True)

    def _on_start_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], is_self_closing: bool
    ) -> None:
        self.total_tags += 1
        tag_id = self._next_id
        self._next_id += 1
        info = _TagInfo(name=tag.lower())

        for k, v in attrs:
            k_norm = (k or "").lower()
            if _is_meaningful_attr(k_norm, v):
                info.has_value_attr = True
                break
        self._tags[tag_id] = info
        if not is_self_closing:
            self._stack.append(tag_id)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        t = tag.lower()
        while self._stack:
            top_id = self._stack.pop()
            if self._tags.get(top_id, _TagInfo(t)).name == t:
                break

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        text = (data or "").strip()
        if not text:
            return
        for tag_id in reversed(self._stack):
            info = self._tags.get(tag_id)
            if info is None:
                continue
            if info.name in SKIP_TEXT_TAGS:
                return
            info.has_direct_text = True
            return

    @property
    def value_tags(self) -> int:
        return sum(1 for t in self._tags.values() if t.has_direct_text or t.has_value_attr)


def _is_meaningful_attr(attr_name: str, attr_value: Optional[str]) -> bool:
    if attr_name not in VALUE_ATTRS:
        return False
    if attr_value is None:
        return False
    v = str(attr_value).strip()
    if not v:
        return False
    if attr_name == "href" and (v == "#" or v.lower().startswith("javascript:")):
        return False
    if attr_name == "src" and v.lower().startswith("data:"):
        return False
    return True


def compute_value_density(html: str) -> Tuple[float, int, int]:
    p = ValueDensityParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        return 0.0, 0, 0
    total = p.total_tags
    value = p.value_tags
    ratio = (value / total) if total > 0 else 0.0
    return ratio, total, value


def normalize_visible_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = TOKEN_RE.findall(text)
    return " ".join(tokens)


def tokenize_for_simhash(text: str, ngram: int = 1) -> List[str]:
    tokens = text.split()
    if not tokens:
        return []
    if ngram <= 1 or len(tokens) < ngram:
        return tokens
    return [" ".join(tokens[i : i + ngram]) for i in range(len(tokens) - ngram + 1)]


def stable_hash64(token: str) -> int:
    d = blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(d, "big", signed=False)


def simhash64(tokens: Iterable[str]) -> int:
    vec = [0] * 64
    seen = False
    for tok in tokens:
        seen = True
        h = stable_hash64(tok)
        for bit in range(64):
            vec[bit] += 1 if ((h >> bit) & 1) else -1
    if not seen:
        return 0
    out = 0
    for bit, v in enumerate(vec):
        if v >= 0:
            out |= 1 << bit
    return out


def hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def get_gpt_html(entry: dict) -> Optional[str]:
    for conv in entry.get("conversations", []):
        if conv.get("from") == "gpt":
            return conv.get("value")
    return None


def resolve_image_path(entry: dict, key: str, image_root: Optional[Path]) -> Optional[str]:
    v = entry.get(key)
    if not isinstance(v, str) or not v.strip():
        return None
    p = Path(v)
    if p.is_absolute():
        return str(p)
    if image_root is not None:
        return str((image_root / p).resolve())
    return str(p)


def get_image_ref(entry: dict, key: str) -> Optional[str]:
    v = entry.get(key)
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()


def extract_member_candidates(image_ref: str) -> List[str]:
    raw = image_ref.strip()
    if not raw:
        return []
    out: List[str] = []
    for cand in [raw.lstrip("./"), Path(raw).name]:
        if cand and cand not in out:
            out.append(cand)
    return out


def extract_chunk_prefix(member_name: str) -> Optional[str]:
    m = CHUNK_MEMBER_RE.match(Path(member_name).name)
    if m is None:
        return None
    return m.group(1)


class TarImageReader:
    def __init__(
        self,
        tars_dir: Path,
        tar_pattern: str,
        lookup_mode: str,
        index_cache: Optional[Path] = None,
    ) -> None:
        self.tars_dir = tars_dir
        self.tar_pattern = tar_pattern
        self.lookup_mode = lookup_mode
        self.index_cache = index_cache
        self.tar_files = sorted(self.tars_dir.glob(self.tar_pattern))
        if not self.tar_files:
            raise ValueError(f"No tar files found in {self.tars_dir} matching pattern {self.tar_pattern}")

        self.chunk_to_tar: Dict[str, str] = {}
        self.member_to_tar: Dict[str, str] = {}
        self._open_tars: Dict[str, tarfile.TarFile] = {}
        self.tars_scanned = 0
        self.cache_loaded = False
        self._build_or_load_index()

    def _build_or_load_index(self) -> None:
        if self.index_cache and self.index_cache.exists():
            try:
                with self.index_cache.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if self.lookup_mode == "chunk-map":
                    self.chunk_to_tar = {str(k): str(v) for k, v in data.get("chunk_to_tar", {}).items()}
                elif self.lookup_mode == "full-index":
                    self.member_to_tar = {str(k): str(v) for k, v in data.get("member_to_tar", {}).items()}
                self.cache_loaded = True
                return
            except Exception:
                # Fall back to rebuilding the index if cache is stale/corrupt.
                pass

        if self.lookup_mode == "chunk-map":
            self._build_chunk_map()
        elif self.lookup_mode == "full-index":
            self._build_full_index()
        else:
            raise ValueError(f"Unknown tar lookup mode: {self.lookup_mode}")

        if self.index_cache is not None:
            self.index_cache.parent.mkdir(parents=True, exist_ok=True)
            with self.index_cache.open("w", encoding="utf-8") as f:
                payload = {
                    "lookup_mode": self.lookup_mode,
                    "chunk_to_tar": self.chunk_to_tar,
                    "member_to_tar": self.member_to_tar,
                }
                json.dump(payload, f, indent=2, ensure_ascii=False)

    def _build_chunk_map(self) -> None:
        for tar_path in self.tar_files:
            self.tars_scanned += 1
            with tarfile.open(tar_path, "r") as tf:
                for m in tf:
                    if not m.isfile():
                        continue
                    chunk = extract_chunk_prefix(m.name)
                    if chunk:
                        self.chunk_to_tar.setdefault(chunk, str(tar_path))

    def _build_full_index(self) -> None:
        for tar_path in self.tar_files:
            self.tars_scanned += 1
            with tarfile.open(tar_path, "r") as tf:
                for m in tf:
                    if not m.isfile():
                        continue
                    norm = m.name.lstrip("./")
                    base = Path(norm).name
                    self.member_to_tar.setdefault(norm, str(tar_path))
                    self.member_to_tar.setdefault(base, str(tar_path))

    def _get_tar(self, tar_path: str) -> tarfile.TarFile:
        tf = self._open_tars.get(tar_path)
        if tf is not None:
            return tf
        tf = tarfile.open(tar_path, "r")
        self._open_tars[tar_path] = tf
        return tf

    def _find_tar_for_member(self, member_name: str) -> Optional[str]:
        if self.lookup_mode == "full-index":
            return self.member_to_tar.get(member_name)
        chunk = extract_chunk_prefix(member_name)
        if chunk is None:
            return None
        return self.chunk_to_tar.get(chunk)

    def _read_member(self, tar_path: str, member_name: str) -> Optional[bytes]:
        tf = self._get_tar(tar_path)
        for cand in [member_name, Path(member_name).name]:
            try:
                f = tf.extractfile(cand)
            except KeyError:
                continue
            if f is None:
                continue
            return f.read()
        return None

    def get_image_bytes(self, image_ref: str) -> Optional[bytes]:
        for member_name in extract_member_candidates(image_ref):
            tar_path = self._find_tar_for_member(member_name)
            if tar_path is None:
                continue
            data = self._read_member(tar_path, member_name)
            if data is not None:
                return data
        return None

    def close(self) -> None:
        for tf in self._open_tars.values():
            try:
                tf.close()
            except Exception:
                pass
        self._open_tars.clear()


def parse_html_features(html: str) -> Tuple[str, str]:
    parser = VisibleTextDOMParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return "", ""
    text = normalize_visible_text(parser.visible_text)
    dom = parser.dom_sequence
    return text, dom


def band_keys(hash64: int, band_bits: int) -> List[Tuple[int, int]]:
    num_bands = 64 // band_bits
    mask = (1 << band_bits) - 1
    out: List[Tuple[int, int]] = []
    for i in range(num_bands):
        shift = i * band_bits
        out.append((i, (hash64 >> shift) & mask))
    return out


def cluster_html_near_duplicates(
    samples: List[Sample],
    text_threshold: int,
    text_threshold_with_dom: int,
    dom_threshold: int,
    band_bits: int,
) -> Dict[int, List[int]]:
    uf = UnionFind(len(samples))
    text_buckets: Dict[Tuple[int, int], List[int]] = {}
    dom_buckets: Dict[Tuple[int, int], List[int]] = {}

    for i, s in enumerate(samples):
        candidates: set[int] = set()

        for key in band_keys(s.text_hash, band_bits):
            for j in text_buckets.get(key, []):
                candidates.add(j)

        for key in band_keys(s.dom_hash, band_bits):
            for j in dom_buckets.get(key, []):
                candidates.add(j)

        for j in candidates:
            other = samples[j]
            text_d = hamming64(s.text_hash, other.text_hash)
            if text_d <= text_threshold:
                uf.union(i, j)
                continue
            if text_d <= text_threshold_with_dom:
                dom_d = hamming64(s.dom_hash, other.dom_hash)
                if dom_d <= dom_threshold:
                    uf.union(i, j)

        for key in band_keys(s.text_hash, band_bits):
            text_buckets.setdefault(key, []).append(i)
        for key in band_keys(s.dom_hash, band_bits):
            dom_buckets.setdefault(key, []).append(i)

    groups: Dict[int, List[int]] = {}
    for i in range(len(samples)):
        r = uf.find(i)
        groups.setdefault(r, []).append(i)
    return groups


def compute_ahash(img: "Image.Image") -> int:
    small = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    out = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            out |= 1 << i
    return out


def compute_dhash(img: "Image.Image") -> int:
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    out = 0
    bit = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            if px[row + x] >= px[row + x + 1]:
                out |= 1 << bit
            bit += 1
    return out


def compute_phash(img: "Image.Image") -> int:
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("pHash requires numpy; install numpy or use --image-hash-type dhash/ahash") from exc

    gray = img.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    arr = np.asarray(gray, dtype=float)
    dct = np.real(np.fft.fft2(arr))
    low = dct[:8, :8]
    flat = low.flatten()
    med = np.median(flat[1:])
    out = 0
    for i, v in enumerate(flat):
        if v >= med:
            out |= 1 << i
    return out


def image_hash_from_bytes(image_bytes: bytes, method: str) -> int:
    if Image is None:
        raise RuntimeError("Pillow is required for image dedup. Install pillow to use --enable-image-dedup")
    with Image.open(io.BytesIO(image_bytes)) as img:
        if method == "ahash":
            return compute_ahash(img)
        if method == "dhash":
            return compute_dhash(img)
        if method == "phash":
            return compute_phash(img)
        raise ValueError(f"Unknown image hash method: {method}")


def read_image_bytes_from_filesystem(path: str) -> Optional[bytes]:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    return p.read_bytes()


def cluster_image_near_duplicates(
    samples: List[Sample],
    threshold: int,
    band_bits: int,
) -> Dict[int, List[int]]:
    uf = UnionFind(len(samples))
    buckets: Dict[Tuple[int, int], List[int]] = {}

    for i, s in enumerate(samples):
        if s.image_hash is None:
            continue

        candidates: set[int] = set()
        for key in band_keys(s.image_hash, band_bits):
            for j in buckets.get(key, []):
                candidates.add(j)

        for j in candidates:
            other = samples[j]
            if other.image_hash is None:
                continue
            if hamming64(s.image_hash, other.image_hash) <= threshold:
                uf.union(i, j)

        for key in band_keys(s.image_hash, band_bits):
            buckets.setdefault(key, []).append(i)

    groups: Dict[int, List[int]] = {}
    for i in range(len(samples)):
        r = uf.find(i)
        groups.setdefault(r, []).append(i)
    return groups


def pick_top_k_per_cluster(
    samples: List[Sample],
    clusters: Dict[int, List[int]],
    k: int,
) -> List[int]:
    kept: List[int] = []
    for members in clusters.values():
        ranked = sorted(
            members,
            key=lambda idx: (
                -samples[idx].quality,
                -samples[idx].total_tags,
                samples[idx].index,
            ),
        )
        kept.extend(ranked[:k])
    kept.sort(key=lambda idx: samples[idx].index)
    return kept


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deduplicate near-duplicate screenshot-to-HTML samples by HTML SimHash and optional image hashes."
    )
    p.add_argument("--input", type=str, required=True, help="Input JSON array file path")
    p.add_argument("--output", type=str, required=True, help="Output deduplicated JSON file path")
    p.add_argument(
        "--report-output",
        type=str,
        default=None,
        help="Optional JSON report path with cluster metadata and dropped mappings",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=-1,
        help="After dedup, keep top-N by quality score (-1 keeps all)",
    )
    p.add_argument(
        "--quality-score-key",
        type=str,
        default="ratio",
        help="Entry key to use as quality score if present; otherwise value-density ratio is computed",
    )
    p.add_argument(
        "--html-cluster-keep-k",
        type=int,
        default=1,
        help="How many samples to keep per HTML near-dup cluster",
    )
    p.add_argument(
        "--html-text-threshold",
        type=int,
        default=3,
        help="Near-dup if Hamming(text_simhash) <= this threshold",
    )
    p.add_argument(
        "--html-text-threshold-with-dom",
        type=int,
        default=6,
        help="Fallback text threshold used jointly with DOM threshold",
    )
    p.add_argument(
        "--html-dom-threshold",
        type=int,
        default=6,
        help="Near-dup if Hamming(text)<=text-th-with-dom AND Hamming(dom)<=this threshold",
    )
    p.add_argument(
        "--simhash-band-bits",
        type=int,
        default=16,
        help="Band size in bits for LSH candidate generation (must divide 64)",
    )
    p.add_argument(
        "--enable-image-dedup",
        action="store_true",
        help="Enable screenshot near-dup dedup using image hashes",
    )
    p.add_argument(
        "--image-key",
        type=str,
        default="image",
        help="Entry key that stores screenshot path (default: image)",
    )
    p.add_argument(
        "--image-source",
        choices=["filesystem", "tar", "auto"],
        default="auto",
        help="Where to load screenshot bytes from for image dedup (default: auto)",
    )
    p.add_argument(
        "--image-root",
        type=str,
        default=None,
        help="Optional root directory for relative image paths",
    )
    p.add_argument(
        "--image-tars-dir",
        type=str,
        default=None,
        help="Directory containing image tar shards used for tar/auto image source",
    )
    p.add_argument(
        "--image-tar-pattern",
        type=str,
        default="images-*.tar",
        help="Glob pattern for tar shards under --image-tars-dir (default: images-*.tar)",
    )
    p.add_argument(
        "--tar-lookup",
        choices=["chunk-map", "full-index"],
        default="chunk-map",
        help="Tar lookup strategy for resolving image members (default: chunk-map)",
    )
    p.add_argument(
        "--tar-index-cache",
        type=str,
        default=None,
        help="Optional JSON cache path for tar index/mapping",
    )
    p.add_argument(
        "--missing-image-policy",
        choices=["keep", "drop", "fail"],
        default="keep",
        help="Behavior when image bytes are missing/unreadable during image dedup",
    )
    p.add_argument(
        "--image-hash-type",
        choices=["phash", "ahash", "dhash"],
        default="phash",
        help="Image hash method for screenshot dedup",
    )
    p.add_argument(
        "--image-threshold",
        type=int,
        default=8,
        help="Near-dup threshold for image hash Hamming distance",
    )
    p.add_argument(
        "--image-cluster-keep-k",
        type=int,
        default=1,
        help="How many samples to keep per image near-dup cluster",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if 64 % args.simhash_band_bits != 0:
        raise ValueError("--simhash-band-bits must divide 64")
    if args.html_cluster_keep_k < 1:
        raise ValueError("--html-cluster-keep-k must be >= 1")
    if args.image_cluster_keep_k < 1:
        raise ValueError("--image-cluster-keep-k must be >= 1")

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report_output) if args.report_output else None
    image_root = Path(args.image_root).resolve() if args.image_root else None
    tar_index_cache = Path(args.tar_index_cache) if args.tar_index_cache else None

    if args.enable_image_dedup and args.image_source == "tar" and not args.image_tars_dir:
        raise ValueError("--image-tars-dir is required when --image-source tar and --enable-image-dedup are set")

    use_tar_source = args.enable_image_dedup and args.image_source in {"tar", "auto"} and args.image_tars_dir
    tar_reader: Optional[TarImageReader] = None
    if use_tar_source:
        tar_reader = TarImageReader(
            tars_dir=Path(args.image_tars_dir).resolve(),
            tar_pattern=args.image_tar_pattern,
            lookup_mode=args.tar_lookup,
            index_cache=tar_index_cache,
        )
        print(
            f"Tar image resolver ready: {len(tar_reader.tar_files)} shard(s), "
            f"lookup={args.tar_lookup}, cache_loaded={tar_reader.cache_loaded}"
        )

    print(f"Loading input JSON: {input_path}")
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected input JSON to be a list")

    print(f"Total entries: {len(data)}")
    samples: List[Sample] = []
    skipped_no_html = 0

    for idx, entry in enumerate(data):
        html = get_gpt_html(entry)
        if html is None:
            skipped_no_html += 1
            continue

        text, dom = parse_html_features(str(html))
        text_tokens = tokenize_for_simhash(text, ngram=1)
        dom_tokens = tokenize_for_simhash(dom, ngram=2)
        text_hash = simhash64(text_tokens)
        dom_hash = simhash64(dom_tokens)

        ratio, total_tags, value_tags = compute_value_density(str(html))
        q = ratio
        key_val = entry.get(args.quality_score_key)
        if key_val is not None:
            try:
                q = float(key_val)
            except Exception:
                pass

        sample_id = str(entry.get("id", idx))
        image_ref = get_image_ref(entry, args.image_key)
        image_path = resolve_image_path(entry, args.image_key, image_root)

        samples.append(
            Sample(
                sample_pos=len(samples),
                index=idx,
                entry=entry,
                sample_id=sample_id,
                html=str(html),
                quality=q,
                text_hash=text_hash,
                dom_hash=dom_hash,
                total_tags=total_tags,
                value_tags=value_tags,
                image_ref=image_ref,
                image_path=image_path,
            )
        )

    print(f"Samples with GPT HTML: {len(samples)}")
    print(f"Skipped (missing GPT HTML): {skipped_no_html}")

    print("Clustering HTML near-duplicates...")
    html_clusters = cluster_html_near_duplicates(
        samples=samples,
        text_threshold=args.html_text_threshold,
        text_threshold_with_dom=args.html_text_threshold_with_dom,
        dom_threshold=args.html_dom_threshold,
        band_bits=args.simhash_band_bits,
    )
    print(f"HTML clusters: {len(html_clusters)}")

    kept_indices = pick_top_k_per_cluster(samples, html_clusters, args.html_cluster_keep_k)
    html_kept_set = set(kept_indices)

    stage_samples = [samples[i] for i in kept_indices]
    image_clusters: Optional[Dict[int, List[int]]] = None
    image_cluster_samples: List[Sample] = stage_samples

    final_global_positions: set[int]

    missing_images = 0
    dropped_missing_images = 0
    dropped_stage_reason: Dict[int, str] = {}

    if args.enable_image_dedup:
        print("Computing screenshot hashes...")

        image_stage_samples: List[Sample] = []
        for s in stage_samples:
            image_bytes: Optional[bytes] = None

            if args.image_source in {"filesystem", "auto"} and s.image_path:
                image_bytes = read_image_bytes_from_filesystem(s.image_path)

            if image_bytes is None and args.image_source in {"tar", "auto"} and tar_reader is not None and s.image_ref:
                image_bytes = tar_reader.get_image_bytes(s.image_ref)

            if image_bytes is None:
                missing_images += 1
                if args.missing_image_policy == "fail":
                    raise RuntimeError(
                        f"Missing image bytes for sample {s.sample_id} (image={s.image_ref}, path={s.image_path})"
                    )
                if args.missing_image_policy == "drop":
                    dropped_missing_images += 1
                    dropped_stage_reason[s.sample_pos] = "image_missing_drop"
                    continue
                image_stage_samples.append(s)
                continue

            try:
                s.image_hash = image_hash_from_bytes(image_bytes, args.image_hash_type)
            except Exception as exc:
                raise RuntimeError(f"Failed to hash image for sample {s.sample_id}: {exc}") from exc

            image_stage_samples.append(s)

        print(f"Image hashes computed. Missing/unreadable images: {missing_images}")
        print("Clustering screenshot near-duplicates...")
        image_cluster_samples = image_stage_samples
        image_clusters = cluster_image_near_duplicates(
            image_cluster_samples,
            threshold=args.image_threshold,
            band_bits=args.simhash_band_bits,
        )
        print(f"Image clusters: {len(image_clusters)}")
        stage_kept = pick_top_k_per_cluster(image_cluster_samples, image_clusters, args.image_cluster_keep_k)
        final_samples = [image_cluster_samples[i] for i in stage_kept]
        final_global_positions = {image_cluster_samples[i].sample_pos for i in stage_kept}
    else:
        final_samples = stage_samples
        final_global_positions = {s.sample_pos for s in final_samples}

    final_samples.sort(
        key=lambda s: (
            -s.quality,
            -s.total_tags,
            s.index,
        )
    )
    if args.top_n >= 0:
        final_samples = final_samples[: args.top_n]
        final_global_positions = {s.sample_pos for s in final_samples}

    output_entries = [s.entry for s in final_samples]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output_entries, f, indent=2, ensure_ascii=False)
    print(f"Saved deduplicated subset to: {output_path}")
    print(f"Final kept entries: {len(output_entries)}")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        html_cluster_map: Dict[int, int] = {}
        for root, members in html_clusters.items():
            for m in members:
                html_cluster_map[m] = root

        image_cluster_map: Dict[int, int] = {}
        if image_clusters is not None:
            for root, members in image_clusters.items():
                for m in members:
                    image_cluster_map[m] = root

        report = {
            "input_count": len(data),
            "samples_with_gpt_html": len(samples),
            "skipped_missing_gpt_html": skipped_no_html,
            "html_cluster_count": len(html_clusters),
            "html_cluster_keep_k": args.html_cluster_keep_k,
            "after_html_dedup_count": len(stage_samples),
            "image_dedup_enabled": args.enable_image_dedup,
            "image_source": args.image_source,
            "missing_image_policy": args.missing_image_policy if args.enable_image_dedup else None,
            "image_cluster_count": len(image_clusters) if image_clusters is not None else None,
            "image_cluster_keep_k": args.image_cluster_keep_k if args.enable_image_dedup else None,
            "missing_image_count": missing_images if args.enable_image_dedup else 0,
            "dropped_missing_image_count": dropped_missing_images if args.enable_image_dedup else 0,
            "tar_lookup_mode": args.tar_lookup if tar_reader is not None else None,
            "tar_shards_scanned": tar_reader.tars_scanned if tar_reader is not None else 0,
            "tar_index_cache_loaded": tar_reader.cache_loaded if tar_reader is not None else False,
            "final_count": len(output_entries),
            "top_n": args.top_n,
            "kept_samples": [
                {
                    "id": s.sample_id,
                    "sample_pos": s.sample_pos,
                    "index": s.index,
                    "quality": s.quality,
                    "total_tags": s.total_tags,
                    "value_tags": s.value_tags,
                    "html_cluster_id": html_cluster_map.get(s.sample_pos),
                    "image_cluster_id": None,
                    "image_path": s.image_path,
                }
                for s in final_samples
            ],
            "dropped_samples": [
                {
                    "id": s.sample_id,
                    "sample_pos": s.sample_pos,
                    "index": s.index,
                    "quality": s.quality,
                    "total_tags": s.total_tags,
                    "value_tags": s.value_tags,
                    "html_cluster_id": html_cluster_map.get(i),
                    "dropped_stage": (
                        dropped_stage_reason.get(i)
                        or (
                            "html_dedup"
                            if i not in html_kept_set
                            else ("image_dedup_or_top_n" if i not in final_global_positions else None)
                        )
                    ),
                }
                for i, s in enumerate(samples)
                if i not in final_global_positions
            ],
        }

        if image_clusters is not None:
            global_to_image_cluster: Dict[int, int] = {}
            for stage_idx, sample in enumerate(image_cluster_samples):
                cluster_id = image_cluster_map.get(stage_idx)
                if cluster_id is not None:
                    global_to_image_cluster[sample.sample_pos] = cluster_id

            for item in report["kept_samples"]:
                item["image_cluster_id"] = global_to_image_cluster.get(item["sample_pos"])
                item.pop("sample_pos", None)

            for item in report["dropped_samples"]:
                item["image_cluster_id"] = global_to_image_cluster.get(item["sample_pos"])
                item.pop("sample_pos", None)
        else:
            for item in report["kept_samples"]:
                item.pop("sample_pos", None)
            for item in report["dropped_samples"]:
                item.pop("sample_pos", None)

        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Saved dedup report to: {report_path}")

    if tar_reader is not None:
        tar_reader.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
