# Table layout and manual-cell validation

Status: reusable geometry and manual-cell inference are implemented; automatic manuscript-layout acceptance is pending a representative annotated corpus. This does not deploy an HTTP service.

## Geometry implementation

`scriptalocal/layout.py` combines adaptive thresholding, vertical morphological evidence and near-vertical Hough segments. It measures ink support along each candidate, handles bounded gaps, merges nearby duplicate-rule proposals at both endpoints, and preserves slanted boundaries as quadrilaterals. A second consolidation pass handles cluster representatives that move during merging. None of these measurements is a confidence probability.

Automatic proposals always require review. Very close/crossing borders that would produce invalid cells return no accepted cells and explicit ambiguity, rather than silently inventing a convenient layout. Near-edge outer rules can exclude margin strips; weaker exterior paper/shadow rules remain in `excluded_outer_rules` for inspection. Equal-strength nearby outer/column rules retain the outermost rule to preserve narrow columns. These heuristics cannot determine a historical table's schema or distinguish every border from handwriting.

Settings are constructor configuration, including support, gap/angle, merge distance, thresholding, outer-rule policy, candidate/manual-divider limits, minimum width and context padding. There is no fixed cell count. Automatic source/geometry-derived cell UUIDs are stable for the same image and geometry; changed boundaries create new identities.

Manual dividers replace the complete proposal. An empty list explicitly means a single cell. Endpoints default to original encoded pixel-edge coordinates and are transformed once into the oriented frame. The caller can explicitly select oriented coordinates for internal work; an unknown frame is rejected. Corrections are not silently reordered; duplicates, crossings, invalid extrapolation and out-of-bounds/nonfinite coordinates fail validation. Logical top/bottom endpoint ordering is checked after orientation, not by comparing encoded y coordinates.

`extract_cell` retains original pixels, including padded context and ink crossing a border. It returns a separate cell mask and both crop-to-oriented and crop-to-encoded affine mappings. The mask does not erase source pixels. Source edges can clip the available context and that fact is recorded. The recognition worker must still prevent adjacent-cell lines from being assigned to the wrong cell and surface overlap warnings; this library alone does not establish HTR-013/014 acceptance.

## Evidence and limits

The CPU suite covers clear, faint, broken, double and slightly slanted borders; exterior margins; narrow interior/edge columns; no-divider inputs; candidate caps; stable order/IDs; explicit corrections and invalid geometry; preserved crossing ink; separate masks; and EXIF-aware crop mappings. Synthetic fixtures test geometry, not handwriting quality.

The uploaded 2418 × 272 row produces 18 proposed cells and 19 boundaries, matching the visible rules on the reviewed overlay. A weaker exterior shadow rule is retained as excluded evidence. The proposal remains needs-review, especially for center fold/blank areas and the caller's column semantics. One image is not sufficient for a robust-layout accuracy claim. Private artifacts are in `data/validation/layout-library/`.

## Reproduction

Build the CPU image using `make validation-cpu-build`, then run `make validation-cpu`. For private diagnostics from the checkout:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --memory 2g --cpus 2 -v "$PWD:/app" \
  scriptalocal-cpu-validation:locked \
  python -m scripts.validation.row_geometry_probe \
  --image data/reference/row-crops/reference-crop.jpg \
  --output data/validation/layout-library
```

The probe saves the unmodified original, context crops, separate masks, geometry/transforms and an overlay in ignored storage. Optional `--corrected-dividers` accepts a local JSON file with a list of `{"top":[x,y],"bottom":[x,y]}` entries. Further settings are available through `LayoutSettings` for the service implementation. The diagnostic process does not classify blank cells or recognize text.

## Manual-cell inference and provenance

The selected initial model profile is recorded in [model-profile.json](validation/model-profile.json). TrOCR uses a pinned Bern revision, only safetensors inference weights, verified file hashes and local-only loading. The bundled Kraken segmentation artifact is identified by SHA-256 in an immutable engine image. The profile records the Bern model-card MIT license and Kraken distribution Apache-2.0 license/source; the bundled segmentation metadata has no separate license field.

The initial segmentation candidate misses the three visible lines when the short cell is magnified to its default 1800-pixel input height. Padding the cell to preserve original pixel scale instead yields three visually isolated lines and real TrOCR output on each. This selects a reproducible baseline for further evaluation; it does not establish German/Latin/name transcription accuracy or superiority to other models.

`manuscript_slice.py` records source script, configuration and recognition/segmentation weight hashes, geometry, bounded-generation truncation, timings and peak CUDA allocation. `export_manuscript_result.py` checks image/configuration identity and converts its raw output into the v1 `JobResult` contract, retaining raw readings and explicit review warnings. Zero-line segmentation cannot become a successful empty export. The exported job UUID identifies a CLI result; **no HTTP job exists**.

The real-cell schema-valid result and text remain private in `data/validation/manuscript-cell-provenance/`; public summaries contain no manuscript text. This completes a functional manually bounded cell, not automatic full-row processing, persistent execution or accuracy acceptance.
