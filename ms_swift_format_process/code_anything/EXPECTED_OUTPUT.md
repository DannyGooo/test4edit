# Expected MS-Swift Output per code-anything Zip

Reference for what `code_anything_to_ms_swift.py` emits when invoked on each
per-category zip listed in `gdown.sh`. Every row in the table below is what the
script auto-detects from the zip; pass `--category-name` / `--prefix` to
override, or `--user-prompt` to change the user message template.

## Common output layout (every zip)

For an invocation of:

```bash
python code_anything_to_ms_swift.py \
    --input-zip <SRC>/<ZIP> \
    -o <OUT>
```

the script writes:

```
<OUT>/ms_swift/<category_name>/
    images-00000.tar              # 5000 PNGs per shard (TarShardWriter), re-encoded RGB PNG
    images-00001.tar
    ...
    meta_data_<prefix>.jsonl      # fenced assistant: "```<lang>\n<code>\n```"
    meta_data_<prefix>_raw.jsonl  # raw assistant:    "<code>"  (no fence)
    .<prefix>_checkpoint.json     # internal resume state; auto-removed on clean finish
```

Each JSONL row:

```json
{"messages":[{"role":"user","content":"<image>\nTask: <task>\nWrite <lang> code that reproduces this image."},
             {"role":"assistant","content":"<code>"}],
 "images":["images-00000.tar/<prefix>_NNNNNN.png"]}
```

User prompt comes from `--user-prompt` (default `<image>\nTask: {task}\nWrite
{language} code that reproduces this image.`). Two placeholders are
substituted: `{language}` comes from each entry's `code_language` field, and
`{task}` is resolved once per run from the auto-detected `category_name` via
`CATEGORY_TO_TASK` in `code_anything_to_ms_swift.py` (override per run with
`--task "<label>"`). Both JSONLs share identical user prompts and image
references; only the assistant content differs.

## Per-zip mapping (auto-detected)

| Source zip                  | `category_name` (output subdir) | `prefix`        | `code_language` | Task (`{task}`)                  | Entries  | User prompt rendered as                                                                                |
| --------------------------- | ------------------------------- | --------------- | --------------- | -------------------------------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `svg.zip`                   | `5_svg`                         | `svg`           | `svg`           | `SVG generation`                 | ~480k    | `<image>\nTask: SVG generation\nWrite svg code that reproduces this image.`                            |
| `chart.zip`                 | `4_chart`                       | `chart`         | `python`        | `Chart generation`               | ~172k    | `<image>\nTask: Chart generation\nWrite python code that reproduces this image.`                       |
| `cad.zip`                   | `12_cad`                        | `cad`           | `python`        | `CAD model generation`           | 50,000   | `<image>\nTask: CAD model generation\nWrite python code that reproduces this image.`                   |
| `11_Formulation.zip`        | `11_Formulation`                | `Formulations`  | `latex`         | `Math formula typesetting`       | 100,000  | `<image>\nTask: Math formula typesetting\nWrite latex code that reproduces this image.`                |
| `8_CircuiTikZ.zip`          | `8_CircuiTikZ`                  | `circuitikz`    | `latex`         | `Circuit diagram generation`     | 100,000  | `<image>\nTask: Circuit diagram generation\nWrite latex code that reproduces this image.`              |
| `ABCNotation.zip`           | `10_ABCNotation`                | `abcnotation`   | `abc`           | `Music notation generation`      | 100,000  | `<image>\nTask: Music notation generation\nWrite abc code that reproduces this image.`                 |
| `Chemical.zip`              | `7_Chemical`                    | `chemical`      | `sdf`           | `Chemical structure generation`  | 100,000  | `<image>\nTask: Chemical structure generation\nWrite sdf code that reproduces this image.`             |
| `Chemical_smiles.zip`       | `7_Chemical_Smiles`             | `smiles`        | `smiles`        | `SMILES generation`              | 100,000  | `<image>\nTask: SMILES generation\nWrite smiles code that reproduces this image.`                      |
| `Biological_structure.zip`  | `7_Biological_Structure_100k`   | `pdb_proteins`  | `python`        | `Protein structure visualization`| 100,000  | `<image>\nTask: Protein structure visualization\nWrite python code that reproduces this image.`        |
| `Math_graphics.zip`         | `3_Math_Graphics`               | `math_graphics` | `latex`         | `Math graphics generation`       | 100,000  | `<image>\nTask: Math graphics generation\nWrite latex code that reproduces this image.`                |
| `Diagram_FlowChart.zip`     | `2_Diagram_FlowChart`           | `diagrams`      | `graphviz`      | `Flowchart and diagram generation`| 100,000 | `<image>\nTask: Flowchart and diagram generation\nWrite graphviz code that reproduces this image.`     |
| `Table.zip`                 | `1_Table`                       | `tables`        | `latex`         | `Table generation`               | 100,000  | `<image>\nTask: Table generation\nWrite latex code that reproduces this image.`                        |
| `Slide_Marp.zip`            | `6_Slides_Marp`                 | `slides`        | `markdown`      | `Marp slide generation`          | 50,000   | `<image>\nTask: Marp slide generation\nWrite markdown code that reproduces this image.`                |
| `Slide_Beamer.zip`          | `6_Slides_Beamer`               | `beamer_slides` | `latex`         | `Beamer slide generation`        | 100,000  | `<image>\nTask: Beamer slide generation\nWrite latex code that reproduces this image.`                 |

### Task-label resolution

The `{task}` substitution is resolved exactly once at the start of each run,
in this precedence:

1. `--task "<label>"` CLI override, if non-empty.
2. `CATEGORY_TO_TASK[category_name]` — the static map shown above lives in
   `code_anything_to_ms_swift.py` (top of file).
3. Fallback to the raw `category_name` (so an unrecognized future zip still
   produces a syntactically valid prompt — just with `Task: 99_NewCategory`
   instead of a human-readable label). When this happens the run logs the
   resolved value under `  task = '...'`, so spot-check the log header to
   confirm the prompt looks right before letting a long run finish.

> Entry counts are the PNG members in each zip; the metadata JSON typically has
> the same count. They are upper bounds — rows with missing images or empty
> code are silently dropped (`skipped_missing_image`, `skipped_missing_code`).

## Unsupported zips

### `Mesh.zip` — excluded from the pipeline

`Mesh.zip` is downloaded by `gdown.sh` but is **not** in the default `ZIPS` list
of `run_code_anything_transform.sh` and will not be converted. Reason: its
metadata stores `image_path` values that end in `.mp4`
(e.g. `10_Mesh/images/Mesh_000000.mp4`) — rendered videos of 3D mesh
visualizations, not still screenshots. The converter is built around PIL/PNG
input and cannot decode MP4. Running it on `Mesh.zip` would log every entry as
`skipped_image_decode_error` and emit no usable tar shards.

Additionally, the default user prompt `"<image>\nWrite python code that
reproduces this image."` is semantically wrong for a video source — there is
no single "image" to reproduce.

**To re-enable Mesh in the future**, one of:

1. Pre-render each `.mp4` to a representative PNG (e.g. the middle frame) and
   repack the zip so `image_path` ends in `.png`.
2. Extend `code_anything_to_ms_swift.py` to detect video extensions, decode
   them with a library like `opencv-python-headless` or `imageio[ffmpeg]`,
   extract a representative frame, and use a video-aware user prompt template
   such as `"<image>\nWrite {language} code that reproduces the 3D mesh shown
   in this rendered frame."`.
3. Switch Mesh's output to ms-swift's native `videos` field (changes the
   JSONL schema and tar contents; requires confirming downstream training
   supports tar-sharded video).

Until one of the above is implemented, force-running the wrapper on Mesh
(e.g. `ZIPS="Mesh.zip" ./run_code_anything_transform.sh`) is the documented
failure path and is expected to produce no output.

## Per-zip notes (only where defaults misbehave)

### `11_Formulation.zip` — assistant content is unfenced LaTeX

Unlike every other zip, the `code` field in `meta_data_Formulations.json`
stores raw LaTeX with no markdown fence (e.g. `\documentclass[varwidth]…`).
The converter handles this correctly:

- `meta_data_Formulations.jsonl` (fenced): the raw LaTeX is **re-wrapped** as
  ```` ```latex\n...\n``` ```` for consistency with the other zips.
- `meta_data_Formulations_raw.jsonl`: identical raw LaTeX.

Note the metadata file uses `Formulations` (plural) while the input image
filenames use `Formulation_NNNNNN.png` (singular). Output image names follow
the metadata prefix and become `Formulations_NNNNNN.png` inside the tars.
Pass `--prefix Formulation` if you prefer the singular form.

### `Math_graphics.zip` — double-fenced code field

A subset of `code` values are wrapped twice, e.g.

````
 ```latex The image can be generated using the following TikZ code:
```latex
\documentclass[tikz,border=3.14mm]{standalone}
...
\end{document}
```  ```
````

`_strip_fence` only peels one layer, so the `_raw.jsonl` assistant content
will still contain leading prose + inner ```` ```latex … ``` ```` fences for
those rows. If clean raw LaTeX is required for training, add a post-filter or
extend `_strip_fence` to handle nested fences.

### `cad.zip` — deep zip prefix

Unlike the other zips, members start with a long path
(`data3/yliu/projects/gui_base/htmlSlicer/output/code-anything/12_cad/...`),
while the metadata `image_path` is relative to `code-anything/`. The
auto-detected `zip_root_prefix` strips the deep path so member lookup works;
no user action needed.

### Slides have heterogeneous numbering schemes

- `Slide_Marp.zip` → image filenames are `Slide_NNNNNN.png` (prefix `Slide`).
- `Slide_Beamer.zip` → image filenames are `NNNNNN.png` (no string prefix in
  the source).

Both still produce sequential `<prefix>_NNNNNN.png` inside the tars because the
converter renames assets to its own canonical sequence (`slides_NNNNNN.png` /
`beamer_slides_NNNNNN.png`). Override with `--prefix` if you want the
canonical name to match the source.

### `Biological_structure.zip` — large code blocks

`pdb_proteins` rows contain very long Python (PDB coordinate arrays); some
exceed 100 KB per row. Throughput will be much lower than e.g. `Table.zip`
both in conversion time and in downstream tokenization.

## Wrapper script

`run_code_anything_transform.sh` iterates the zip list from `gdown.sh` and
invokes the converter once per zip with `--resume`. Each zip lands in its
own `ms_swift/<category_name>/` and a single failure does not block the rest.
Set `OUT_DIR` / `SRC_DIR` / `ZIPS` env vars to override defaults.

## Verifying a finished category

```bash
python code_anything_to_ms_swift.py --input-zip <SRC>/<ZIP> -o <OUT> --verify
```

prints a JSON object with `metadata_entries`, `metadata_raw_entries`, and
`total_images` for the matching `<category_name>/`. A healthy run has all
three equal and `mismatched_files == []`.
