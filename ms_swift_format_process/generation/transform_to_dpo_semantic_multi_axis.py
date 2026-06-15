#!/usr/bin/env python3
"""
Transform ms_swift JSONL dataset to DPO format with MULTI-AXIS semantic rejected
samples. Sibling to transform_to_dpo_semantic.py, but perturbs many more visual
axes so the rejected HTML differs from the chosen HTML along typography, layout,
effects, colors AND sizes — not just colors and sizes.

Perturbation categories (all default-on, toggleable via --categories):

  1. colors      — hex (#rgb/#rrggbb/#rrggbbaa), rgb()/rgba(), hsl()/hsla(),
                   and 31 named CSS colors remapped to other colors.
  2. dimensions  — numeric values with px/em/rem/%/vh/vw/pt/cm/mm/in/deg
                   scaled by a random multiplier {0.25x, 0.5x, 2x, 3x, 4x}.
  3. opacity     — 'opacity', 'fill-opacity', 'stroke-opacity' unitless floats
                   remapped to a visibly different value in [0.1, 1.0].
  4. fonts       — 'font-family' values replaced with a visually distinct
                   alternative (serif/sans-serif/monospace/cursive families).
  5. font_weight — 'font-weight' remapped (normal<->bold, 400->900, …).
  6. transform   — 'transform' function arguments perturbed in place
                   (rotate/scale/translate/skew).
  7. display     — 'display' keyword (and flex-direction / justify-content /
                   align-items) swapped via a keyword scramble table.
  8. img_dims    — HTML <img width="X" height="Y"> attributes scaled by the
                   same dimension multipliers. This is the only perturbation
                   that operates OUTSIDE <style>/style="..." scope.

The rejected HTML is therefore:
  - Syntactically valid and structurally identical to the chosen HTML
  - Visually wrong across multiple independent axes
  - A stronger DPO signal than single-axis (colors+sizes only) perturbation

All perturbations except img_dims are scoped to CSS text inside <style>…</style>
blocks and style="..." / style='...' attributes, so text content, class names,
and resource URLs are never touched.

Input format (JSONL, one JSON per line):
  {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Output format (DPO JSONL, one JSON per line):
  {
    "query": "<image>\\nUser prompt...",
    "response": "<!DOCTYPE html>...",
    "rejected_response": "<!DOCTYPE html>... [semantic perturbation]",
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Usage:
  python3 transform_to_dpo_semantic_multi_axis.py --input data.jsonl --output data_dpo.jsonl \
      [--limit N] [--seed S] [--perturb-rate 0.7] \
      [--categories colors,dimensions,opacity,fonts,font_weight,transform,display,img_dims]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List, Set


# =============================================================================
# Color / dimension vocabularies and regexes
# =============================================================================

# CSS named colors we are willing to substitute. Limited to visually distinct
# colors so every swap is a meaningful perceptual change.
NAMED_COLORS = [
    'black', 'white', 'red', 'green', 'blue', 'yellow', 'orange', 'purple',
    'pink', 'brown', 'gray', 'grey', 'cyan', 'magenta', 'lime', 'navy',
    'teal', 'olive', 'maroon', 'silver', 'gold', 'indigo', 'violet', 'tan',
    'beige', 'coral', 'salmon', 'khaki', 'crimson', 'aqua', 'turquoise',
]
NAMED_COLOR_SET = {c.lower() for c in NAMED_COLORS}

# Named-color synonyms — swapping between these produces no visible change,
# so pick_different_named_color() filters them out.
NAMED_COLOR_SYNONYMS: Dict[str, str] = {
    'aqua': 'cyan',
    'cyan': 'aqua',
    'grey': 'gray',
    'gray': 'grey',
}

# Hex colors (#rgb, #rgba, #rrggbb, #rrggbbaa). Anchored alternation with
# longest-first so '#abc123de' isn't truncated to '#abc123d'. The trailing
# negative lookahead rules out matching inside a longer hex literal.
HEX_COLOR_RE = re.compile(
    r'#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])'
)

# rgb() / rgba() functional notation
RGB_COLOR_RE = re.compile(r'rgba?\(\s*[\d.,\s%/]+\)', re.IGNORECASE)

# hsl() / hsla() functional notation — the '%' is optional for the hue but
# required for saturation/lightness in practice, so we accept any mix.
HSL_COLOR_RE = re.compile(r'hsla?\(\s*[\d.,\s%/deg]+\)', re.IGNORECASE)

# Named colors — matched with word boundaries so they don't eat class names
NAMED_COLOR_RE = re.compile(
    r'\b(' + '|'.join(sorted(NAMED_COLORS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

# Dimension values — numbers with a CSS length or angle unit. The lookbehind
# prevents matching inside words/identifiers (e.g. class names with digits).
# 'deg' is now included so gradient angles and unit-bearing transform
# arguments are scaled the same way as sizes.
DIMENSION_RE = re.compile(
    r'(?<![\w.#-])(-?\d+(?:\.\d+)?)(px|em|rem|vh|vw|pt|cm|mm|in|deg|%)\b',
    re.IGNORECASE,
)

# Discrete scale multipliers for dimension perturbation — every multiplier is
# far enough from 1.0 to be visually obvious.
DIM_MULTIPLIERS = [0.25, 0.5, 2.0, 3.0, 4.0]

# -----------------------------------------------------------------------------
# Multi-axis perturbation vocabularies
# -----------------------------------------------------------------------------

# Opacity / fill-opacity / stroke-opacity: property-anchored unitless float.
OPACITY_RE = re.compile(
    r'(\b(?:opacity|fill-opacity|stroke-opacity)\s*:\s*)(\d*\.?\d+)',
    re.IGNORECASE,
)

# font-family: property-anchored value (stops at ; or } or newline).
FONT_FAMILY_RE = re.compile(
    r'(\bfont-family\s*:\s*)([^;}\n]+)',
    re.IGNORECASE,
)

# font-weight: property-anchored value. Accepts keywords or 100-900.
FONT_WEIGHT_RE = re.compile(
    r'(\bfont-weight\s*:\s*)(normal|bold|lighter|bolder|[1-9]00)\b',
    re.IGNORECASE,
)

# transform: property-anchored value block (entire function list).
TRANSFORM_RE = re.compile(
    r'(\btransform\s*:\s*)([^;}\n]+)',
    re.IGNORECASE,
)

# Single transform function calls inside a transform: block, e.g. rotate(45deg),
# scale(1.2), translate(10px, 20px). Matched with the function name captured so
# we can dispatch per-function perturbation logic.
TRANSFORM_FN_RE = re.compile(
    r'(rotate|rotateX|rotateY|rotateZ|scale|scaleX|scaleY|translate|translateX|translateY|skew|skewX|skewY)\(\s*([^)]*)\)',
    re.IGNORECASE,
)

# display keyword. Anchored so we only catch 'display:' values, not text.
DISPLAY_RE = re.compile(
    r'(\bdisplay\s*:\s*)(flex|grid|block|inline|inline-block|inline-flex|inline-grid|none|table|table-cell)\b',
    re.IGNORECASE,
)

# flex-direction / justify-content / align-items keyword values.
FLEX_DIRECTION_RE = re.compile(
    r'(\bflex-direction\s*:\s*)(row|row-reverse|column|column-reverse)\b',
    re.IGNORECASE,
)
JUSTIFY_CONTENT_RE = re.compile(
    r'(\bjustify-content\s*:\s*)(flex-start|flex-end|center|space-between|space-around|space-evenly|start|end)\b',
    re.IGNORECASE,
)
ALIGN_ITEMS_RE = re.compile(
    r'(\balign-items\s*:\s*)(flex-start|flex-end|center|stretch|baseline|start|end)\b',
    re.IGNORECASE,
)

# <img width=...> and <img height=...> HTML attribute — perturbed outside CSS.
IMG_DIM_ATTR_RE = re.compile(
    r'(<img\b[^>]*?\s(?:width|height)\s*=\s*["\']?)(\d+)(?:px)?(["\']?)',
    re.IGNORECASE,
)

# Font-family replacement pool. Each entry is a visually distinct CSS stack.
FONT_FAMILY_POOL: List[str] = [
    "'Times New Roman', Times, serif",
    "Arial, Helvetica, sans-serif",
    "'Courier New', Courier, monospace",
    "Georgia, 'Times New Roman', serif",
    "'Comic Sans MS', 'Comic Sans', cursive",
    "Impact, 'Arial Black', sans-serif",
    "Verdana, Geneva, sans-serif",
    "'Lucida Console', Monaco, monospace",
]

# font-weight swap table. Keys lowercased; swap produces a visibly different
# weight (lightest <-> heaviest, medium <-> extreme).
FONT_WEIGHT_SWAP: Dict[str, str] = {
    'normal': 'bold',
    'bold': 'normal',
    'lighter': 'bolder',
    'bolder': 'lighter',
    '100': '900',
    '200': '800',
    '300': '700',
    '400': '900',
    '500': '100',
    '600': '200',
    '700': '300',
    '800': '200',
    '900': '400',
}

# display keyword scramble — each value maps to a visually different display.
DISPLAY_SWAP: Dict[str, str] = {
    'flex': 'block',
    'grid': 'block',
    'block': 'inline-block',
    'inline': 'block',
    'inline-block': 'block',
    'inline-flex': 'block',
    'inline-grid': 'block',
    'none': 'block',
    'table': 'block',
    'table-cell': 'inline-block',
}

FLEX_DIRECTION_SWAP: Dict[str, str] = {
    'row': 'column',
    'row-reverse': 'column-reverse',
    'column': 'row',
    'column-reverse': 'row-reverse',
}

JUSTIFY_CONTENT_SWAP: Dict[str, str] = {
    'flex-start': 'flex-end',
    'flex-end': 'flex-start',
    'start': 'end',
    'end': 'start',
    'center': 'space-between',
    'space-between': 'center',
    'space-around': 'flex-start',
    'space-evenly': 'flex-end',
}

ALIGN_ITEMS_SWAP: Dict[str, str] = {
    'flex-start': 'flex-end',
    'flex-end': 'flex-start',
    'start': 'end',
    'end': 'start',
    'center': 'baseline',
    'stretch': 'center',
    'baseline': 'stretch',
}


# -----------------------------------------------------------------------------
# Category flags
# -----------------------------------------------------------------------------

ALL_CATEGORIES = (
    'colors', 'dimensions', 'opacity', 'fonts', 'font_weight',
    'transform', 'display', 'img_dims',
)


class CategoryFlags:
    """Per-category enable flags. Default = all on."""
    __slots__ = ALL_CATEGORIES

    def __init__(self, enabled: Optional[set] = None) -> None:
        on = enabled if enabled is not None else set(ALL_CATEGORIES)
        for name in ALL_CATEGORIES:
            setattr(self, name, name in on)

    @classmethod
    def from_csv(cls, csv: Optional[str]) -> 'CategoryFlags':
        if csv is None or csv.strip() == '' or csv.strip().lower() == 'all':
            return cls()
        raw = {p.strip().lower() for p in csv.split(',') if p.strip()}
        unknown = raw - set(ALL_CATEGORIES)
        if unknown:
            raise ValueError(
                f"Unknown category/categories: {sorted(unknown)}. "
                f"Valid: {sorted(ALL_CATEGORIES)}"
            )
        return cls(enabled=raw)


# =============================================================================
# Perturbation primitives
# =============================================================================

def random_hex_color() -> str:
    """Return a random 6-digit hex color like '#a3f92c'."""
    return '#{:06x}'.format(random.randint(0, 0xFFFFFF))


def random_rgb_color() -> str:
    """Return a random rgb() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    return f'rgb({r}, {g}, {b})'


def random_rgba_color() -> str:
    """Return a random rgba() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    a = round(random.uniform(0.1, 1.0), 2)
    return f'rgba({r}, {g}, {b}, {a})'


def random_hsl_color() -> str:
    """Return a random hsl() color string."""
    h = random.randint(0, 359)
    s = random.randint(20, 100)
    l = random.randint(15, 85)
    return f'hsl({h}, {s}%, {l}%)'


def random_hsla_color() -> str:
    """Return a random hsla() color string."""
    h = random.randint(0, 359)
    s = random.randint(20, 100)
    l = random.randint(15, 85)
    a = round(random.uniform(0.2, 1.0), 2)
    return f'hsla({h}, {s}%, {l}%, {a})'


def pick_different_named_color(current: str) -> str:
    """Pick a random named color that is neither identical nor a visual
    synonym of `current`."""
    current_l = current.lower()
    synonym = NAMED_COLOR_SYNONYMS.get(current_l)
    choices = [
        c for c in NAMED_COLORS
        if c.lower() != current_l and c.lower() != synonym
    ]
    return random.choice(choices)


def scale_dimension(value: float, unit: str) -> str:
    """Scale a dimension by a random multiplier from DIM_MULTIPLIERS."""
    factor = random.choice(DIM_MULTIPLIERS)
    new_val = value * factor
    # Preserve integer formatting when possible
    if abs(new_val - round(new_val)) < 1e-9:
        return f'{int(round(new_val))}{unit}'
    return f'{new_val:g}{unit}'


def perturb_opacity_value(current: str) -> str:
    """Remap an opacity float to a value that differs from the original by
    at least 0.2 (clamped to [0.1, 1.0])."""
    try:
        cur = float(current)
    except ValueError:
        cur = 1.0
    for _ in range(10):
        candidate = round(random.uniform(0.1, 1.0), 2)
        if abs(candidate - cur) >= 0.2:
            return f'{candidate:g}'
    # Fallback: complement
    complement = round(max(0.1, min(1.0, 1.0 - cur)), 2)
    return f'{complement:g}'


def pick_different_font_family(current: str) -> str:
    """Pick a replacement font-family value that differs from the current
    one. Comparison is done on the first identifier of each stack so that
    'Arial, Helvetica' and 'Arial, sans-serif' are treated as the same."""
    def head(s: str) -> str:
        s = s.strip().lower()
        first = s.split(',')[0].strip().strip("'\"")
        return first

    cur_head = head(current)
    choices = [f for f in FONT_FAMILY_POOL if head(f) != cur_head]
    return random.choice(choices) if choices else random.choice(FONT_FAMILY_POOL)


def perturb_font_weight(current: str) -> str:
    """Swap a font-weight token via FONT_WEIGHT_SWAP; fall back to 'bold'."""
    return FONT_WEIGHT_SWAP.get(current.lower(), 'bold')


def perturb_display_keyword(current: str) -> str:
    return DISPLAY_SWAP.get(current.lower(), 'block')


def perturb_flex_direction(current: str) -> str:
    return FLEX_DIRECTION_SWAP.get(current.lower(), 'column')


def perturb_justify_content(current: str) -> str:
    return JUSTIFY_CONTENT_SWAP.get(current.lower(), 'flex-end')


def perturb_align_items(current: str) -> str:
    return ALIGN_ITEMS_SWAP.get(current.lower(), 'flex-end')


# =============================================================================
# CSS-text perturbation
# =============================================================================

class PerturbStats:
    """Mutable counters threaded through the regex callbacks so we can tell
    which entries actually received a meaningful perturbation."""
    __slots__ = (
        'colors', 'dimensions', 'opacity', 'fonts', 'font_weights',
        'transforms', 'display', 'img_dims',
    )

    def __init__(self) -> None:
        self.colors = 0
        self.dimensions = 0
        self.opacity = 0
        self.fonts = 0
        self.font_weights = 0
        self.transforms = 0
        self.display = 0
        self.img_dims = 0

    def any_nonzero(self) -> bool:
        return any(getattr(self, s) for s in self.__slots__)

    def as_label(self) -> str:
        return (
            f"semantic_c{self.colors}_d{self.dimensions}_o{self.opacity}"
            f"_f{self.fonts}_fw{self.font_weights}_t{self.transforms}"
            f"_dp{self.display}_img{self.img_dims}"
        )


def _perturb_transform_block(block: str, perturb_rate: float, stats: PerturbStats) -> str:
    """Perturb individual rotate/scale/translate/skew function calls inside a
    transform: value block. Each call is rolled independently."""

    def sub_fn(m: 're.Match[str]') -> str:
        name = m.group(1)
        args = m.group(2).strip()
        name_l = name.lower()
        if random.random() >= perturb_rate:
            return m.group(0)
        stats.transforms += 1

        if name_l.startswith('rotate') or name_l.startswith('skew'):
            # Args look like '45deg' or '0.5turn'. Add a large offset.
            num_m = re.match(r'(-?\d+(?:\.\d+)?)\s*(deg|rad|turn|grad)?', args, re.IGNORECASE)
            if num_m:
                val = float(num_m.group(1))
                unit = num_m.group(2) or 'deg'
                offset = random.choice([45, 90, 135, 180, -45, -90])
                new_val = val + offset
                if abs(new_val - round(new_val)) < 1e-9:
                    new_val_s = f'{int(round(new_val))}'
                else:
                    new_val_s = f'{new_val:g}'
                return f'{name}({new_val_s}{unit})'
            return m.group(0)

        if name_l.startswith('scale'):
            parts = [p.strip() for p in args.split(',')]
            new_parts = []
            for p in parts:
                try:
                    v = float(p)
                except ValueError:
                    new_parts.append(p)
                    continue
                factor = random.choice([0.3, 0.5, 1.8, 2.5, 3.0])
                nv = v * factor
                if abs(nv - round(nv)) < 1e-9:
                    new_parts.append(f'{int(round(nv))}')
                else:
                    new_parts.append(f'{nv:g}')
            return f'{name}({", ".join(new_parts)})'

        if name_l.startswith('translate'):
            # Args are dimension values (px/em/%) — re-use scale_dimension.
            def sub_dim_local(dm: 're.Match[str]') -> str:
                value = float(dm.group(1))
                unit = dm.group(2)
                return scale_dimension(value, unit)
            new_args = DIMENSION_RE.sub(sub_dim_local, args)
            return f'{name}({new_args})'

        return m.group(0)

    return TRANSFORM_FN_RE.sub(sub_fn, block)


def perturb_css_text(
    css: str,
    perturb_rate: float,
    stats: PerturbStats,
    flags: CategoryFlags,
) -> str:
    """Apply all enabled perturbations to a CSS text blob."""

    # --- colors: hex, rgb/rgba, hsl/hsla, named ------------------------------
    if flags.colors:
        def sub_hex(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_hex_color()
            return m.group(0)

        def sub_rgb(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_rgba_color() if 'rgba' in m.group(0).lower() else random_rgb_color()
            return m.group(0)

        def sub_hsl(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_hsla_color() if 'hsla' in m.group(0).lower() else random_hsl_color()
            return m.group(0)

        def sub_named(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return pick_different_named_color(m.group(0))
            return m.group(0)

        # Order matters: hex first (digits), then rgb, then hsl, then named.
        css = HEX_COLOR_RE.sub(sub_hex, css)
        css = RGB_COLOR_RE.sub(sub_rgb, css)
        css = HSL_COLOR_RE.sub(sub_hsl, css)
        css = NAMED_COLOR_RE.sub(sub_named, css)

    # --- opacity / fill-opacity / stroke-opacity -----------------------------
    if flags.opacity:
        def sub_opacity(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.opacity += 1
                return m.group(1) + perturb_opacity_value(m.group(2))
            return m.group(0)

        css = OPACITY_RE.sub(sub_opacity, css)

    # --- font-family ---------------------------------------------------------
    if flags.fonts:
        def sub_font_family(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.fonts += 1
                return m.group(1) + pick_different_font_family(m.group(2))
            return m.group(0)

        css = FONT_FAMILY_RE.sub(sub_font_family, css)

    # --- font-weight ---------------------------------------------------------
    if flags.font_weight:
        def sub_font_weight(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.font_weights += 1
                return m.group(1) + perturb_font_weight(m.group(2))
            return m.group(0)

        css = FONT_WEIGHT_RE.sub(sub_font_weight, css)

    # --- transform functions (rotate/scale/translate/skew) ------------------
    if flags.transform:
        def sub_transform(m: 're.Match[str]') -> str:
            prefix = m.group(1)
            block = m.group(2)
            return prefix + _perturb_transform_block(block, perturb_rate, stats)

        css = TRANSFORM_RE.sub(sub_transform, css)

    # --- display / flex-direction / justify-content / align-items ------------
    if flags.display:
        def sub_display(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_display_keyword(m.group(2))
            return m.group(0)

        def sub_flex_dir(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_flex_direction(m.group(2))
            return m.group(0)

        def sub_justify(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_justify_content(m.group(2))
            return m.group(0)

        def sub_align(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_align_items(m.group(2))
            return m.group(0)

        css = DISPLAY_RE.sub(sub_display, css)
        css = FLEX_DIRECTION_RE.sub(sub_flex_dir, css)
        css = JUSTIFY_CONTENT_RE.sub(sub_justify, css)
        css = ALIGN_ITEMS_RE.sub(sub_align, css)

    # --- dimensions (length + deg units) -------------------------------------
    # Dimensions are perturbed LAST so that transform blocks have their own
    # function-scoped handling applied first (otherwise the generic dimension
    # regex would rescale the inside of rotate()/translate() twice).
    if flags.dimensions:
        def sub_dim(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.dimensions += 1
                value = float(m.group(1))
                unit = m.group(2)
                return scale_dimension(value, unit)
            return m.group(0)

        css = DIMENSION_RE.sub(sub_dim, css)

    return css


# =============================================================================
# HTML scoping: <style> blocks, style="..." attrs, and (for img_dims) the
# whole HTML.
# =============================================================================

STYLE_BLOCK_RE = re.compile(r'(<style\b[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
STYLE_ATTR_SINGLE_RE = re.compile(r"style\s*=\s*'([^']*)'", re.IGNORECASE)


def perturb_html(
    html: str,
    perturb_rate: float,
    flags: CategoryFlags,
) -> Tuple[str, PerturbStats]:
    """Apply all enabled perturbations to an HTML document. CSS-scoped
    categories are routed through perturb_css_text; img_dims is applied to
    the entire document."""
    stats = PerturbStats()

    def sub_style_block(m: 're.Match[str]') -> str:
        return m.group(1) + perturb_css_text(m.group(2), perturb_rate, stats, flags) + m.group(3)

    def sub_style_attr_double(m: 're.Match[str]') -> str:
        return 'style="' + perturb_css_text(m.group(1), perturb_rate, stats, flags) + '"'

    def sub_style_attr_single(m: 're.Match[str]') -> str:
        return "style='" + perturb_css_text(m.group(1), perturb_rate, stats, flags) + "'"

    html = STYLE_BLOCK_RE.sub(sub_style_block, html)
    html = STYLE_ATTR_RE.sub(sub_style_attr_double, html)
    html = STYLE_ATTR_SINGLE_RE.sub(sub_style_attr_single, html)

    # <img width="..." height="..."> — operates on the whole document.
    if flags.img_dims:
        def sub_img_dim(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.img_dims += 1
                try:
                    value = float(m.group(2))
                except ValueError:
                    return m.group(0)
                new_val = scale_dimension(value, '')
                return m.group(1) + new_val + m.group(3)
            return m.group(0)

        html = IMG_DIM_ATTR_RE.sub(sub_img_dim, html)

    return html, stats


# =============================================================================
# ms_swift entry processing
# =============================================================================

def process_entry(
    entry: Dict[str, Any],
    perturb_rate: float,
    flags: CategoryFlags,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional['PerturbStats']]:
    """Process a single ms_swift entry → DPO entry with multi-axis rejection."""
    user_msg = None
    assistant_msg = None

    for msg in entry.get("messages", []):
        role = msg.get("role", "")
        if role == "user":
            user_msg = msg.get("content", "")
        elif role == "assistant":
            assistant_msg = msg.get("content", "")

    if not user_msg:
        return None, "no_user_message", None
    if not assistant_msg:
        return None, "no_assistant_response", None

    rejected, stats = perturb_html(assistant_msg, perturb_rate, flags)

    # Skip entries where nothing could be perturbed along any enabled axis.
    if not stats.any_nonzero():
        return None, "no_perturbable_content", None

    # Skip pathological no-op (identical output). Very rare but worth catching.
    if rejected == assistant_msg:
        return None, "perturbation_no_op", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected,
        "images": entry.get("images", []),
    }
    return dpo_entry, None, stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL dataset to DPO format with multi-axis semantic rejected samples"
    )
    parser.add_argument("--input", "-i", required=True, help="Input JSONL file path (ms_swift format)")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL file path (DPO format)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of entries (default: all)")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--perturb-rate",
        type=float,
        default=0.7,
        help="Per-value probability of perturbation [0.0, 1.0]. Higher = more aggressive (default: 0.7)",
    )
    parser.add_argument(
        "--categories",
        "-c",
        type=str,
        default="all",
        help=(
            "Comma-separated list of perturbation categories to enable. "
            "Valid: " + ",".join(ALL_CATEGORIES) + ". "
            "Default: 'all' (every category enabled)."
        ),
    )

    args = parser.parse_args()

    if not (0.0 < args.perturb_rate <= 1.0):
        print(f"Error: --perturb-rate must be in (0.0, 1.0], got {args.perturb_rate}", file=sys.stderr)
        sys.exit(1)

    try:
        flags = CategoryFlags.from_csv(args.categories)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    total = 0
    written = 0
    skipped = 0
    totals: Dict[str, int] = {name: 0 for name in ALL_CATEGORIES}
    # 'dimensions' in PerturbStats is the slot name; categories list also has it.
    stat_name_to_slot = {
        'colors': 'colors',
        'dimensions': 'dimensions',
        'opacity': 'opacity',
        'fonts': 'fonts',
        'font_weight': 'font_weights',
        'transform': 'transforms',
        'display': 'display',
        'img_dims': 'img_dims',
    }
    skip_reasons: Dict[str, int] = {}

    enabled_names = [c for c in ALL_CATEGORIES if getattr(flags, c)]

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print(f"Perturb rate: {args.perturb_rate}")
    print(f"Enabled categories ({len(enabled_names)}/{len(ALL_CATEGORIES)}): {','.join(enabled_names)}")
    print("\nProcessing entries...")

    with open(args.input, "r", encoding="utf-8") as fin, \
         open(args.output, "w", encoding="utf-8") as fout:

        for i, line in enumerate(fin):
            if args.limit and written >= args.limit:
                break

            line = line.strip()
            if not line:
                continue

            total += 1

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping line {i + 1} (invalid JSON): {e}", file=sys.stderr)
                skipped += 1
                skip_reasons["invalid_json"] = skip_reasons.get("invalid_json", 0) + 1
                continue

            dpo_entry, skip_reason, stats = process_entry(entry, args.perturb_rate, flags)

            if skip_reason:
                skipped += 1
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            else:
                fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                written += 1
                for cat_name, slot in stat_name_to_slot.items():
                    totals[cat_name] += getattr(stats, slot)

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries...")

    print("\n" + "=" * 60)
    print("TRANSFORMATION STATISTICS (SEMANTIC MULTI-AXIS)")
    print("=" * 60)
    print(f"Total lines processed: {total}")
    print(f"Successfully transformed: {written}")
    print(f"Skipped: {skipped}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")

    if written > 0:
        print(f"\nPerturbation totals (per enabled category):")
        header = f"  {'category':<14} {'total':>10} {'avg/entry':>12}"
        print(header)
        print(f"  {'-' * 12:<14} {'-' * 8:>10} {'-' * 10:>12}")
        for cat in ALL_CATEGORIES:
            if not getattr(flags, cat):
                continue
            t = totals[cat]
            avg = t / written
            print(f"  {cat:<14} {t:>10} {avg:>12.2f}")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
