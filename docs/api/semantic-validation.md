# Executable v1 semantic contract

Status: design/library behavior verified offline; OCR HTTP routes remain undeployed. JSON Schema describes structure but cannot fully enforce cross-field values, ordering, source bounds or newline assembly. Validate both structure and semantics before importing a result. The authoritative executable checks are `scriptalocal/contracts.py`, tested against Pydantic 2.11.4 and Pillow 11.2.1.

## Offline command

```bash
python -m scripts.validate_contract --model JobResult /path/to/result.json
```

The command emits a validity report without echoing input text, calls no network/model/HTTP/database service, and exits 1 on semantic failure. Its default 2 MiB input cap is a configurable local CLI bound, not an agreed/deployed OCR response limit. Other supported models include JobStatus, JobAccepted, JobOptions, RevisionInput, ErrorResponse, ModelInventory and Health. The accompanying offline ZIP includes the exact source, dependencies, constraints and contract documentation; use a disposable Python 3.11 environment rather than changing an application runtime.

## Geometry and order

- Affine arrays are row-major `[a,b,c,d,e,f]`, with `x'=a*x+b*y+c`, `y'=d*x+e*y+f`. Identity is `[1,0,0,0,1,0]`. Crop origin `(100,200)` maps to a scan with `[1,0,100,0,1,200]`. These map named frames; result polygons/baselines already use the original encoded crop frame.
- Encoded width/height are positive integers. EXIF 5–8 swaps oriented dimensions; 1–4 preserves them. The original-to-oriented affine must exactly match the specified EXIF orientation, as tested for all eight orientations. Additional affine transforms must be invertible.
- Coordinates are finite and nonnegative, no larger than encoded width/height. Pixel-edge convention permits x=width and y=height. Polygons have at least three points and nonzero signed area magnitude. Baselines have at least two distinct points. This is not a full self-intersection/topology or handwriting-assignment validator; review geometry ambiguity separately.
- Cell order is consecutive from zero and matches array order; the same holds for each cell's line order. Every cell and line UUID is unique across the result. Order describes the oriented reading view; it need not be ascending encoded x/y after EXIF rotation.

## Text, raw readings and warnings

At line level:

| Effective state | Required effective text | Additional checks |
| --- | --- | --- |
| recognized | Nonempty, non-whitespace string | Raw recognized output needs model provenance |
| blank | Empty string | Never inferred from a failed/empty recognition call |
| unreadable or failed | null | Own nonempty warnings required |
| needs-review | String, including partial/empty, or null | Own nonempty warnings required |

`raw_state/raw_text` are line fields. Raw recognized text must be nonempty/non-whitespace and include recognition model provenance. Raw blank text is `""`; raw unreadable/failed text is null. Effective state/text equal raw state/text unless a saved correction supplies the effective values. A correction supplies actor/timestamp plus recognized, unreadable or needs-review state, and obeys its own text rules. Corrections do not alter raw fields. Uncertain effective lines require their own warning even when a cell/job also has warnings.

At cell level:

- A blank cell has `text:""` and `lines:[]` and retains its position.
- A recognized cell has at least one line and every line is recognized.
- A nonblank cell without lines has null text. A failed cell with no lines is allowed with its own warning. A no-line nonblank segmentation outcome must not be relabeled blank or recognized.
- With lines, cell text is null if any effective line text is null; otherwise it is the exact LF join of all effective line strings, including empty strings. Do not trim, normalize spelling/names or drop unknown lines. Partial evidence remains in the line collection.
- Effective failed, unreadable and needs-review cells each require their own nonempty warning list. Nested line/job warnings do not replace this cell warning. For a cell with partial processing, assembly preserves line evidence under the LF/null rule; a cell state alone is not permission to discard known lines or manufacture text.
- No extra cell-level raw fields exist in v1. Raw cell interpretation is assembled from immutable line evidence and the saved raw result snapshot.

At job-result level, warnings anywhere or a cell/line state outside recognized/blank requires `review_required:true`. The converse is permitted: a service/operator policy may require review even without an additional schema-detectable issue. Every warning has a bounded nonempty code/message. Model revision and weights hash are immutable provenance, not confidence percentages.

The state rules above establish validity. They do not establish handwriting truth, correct column assignment or consent to publish a reading. Registers still requires explicit mapping/review and separate attributed unreviewed drafts.

## Status and revisions

Only succeeded jobs have `stage:complete`, and succeeded must have that stage. Failed status requires a nonempty error_code; other statuses must not carry one. updated_at must not precede created_at. Transport HTTP errors use ErrorResponse, separately from terminal job status.

Revision requests carry expected_revision and either text corrections or divider geometry, never both together. Line IDs within a text correction request are unique. Empty dividers explicitly request one cell; absent/null dividers request detection/default layout. Coordinate/target ownership and optimistic-concurrency checks additionally require stored job context and cannot be proved by a standalone request model.

The standalone validator enforces only these documented model invariants. It does not prove authorization, model inventory readiness, current remote revision identity, cancellation fencing, idempotent application publication or source-file identity. Check those against durable caller/server state.
