# Response to the registers integration handover

Prepared 12 November 2026 after receiving `SCRIPTALOCAL-INTEGRATION.md` and its fixture README through the scoped upload API. Both received file hashes match the owner's supplied SHA-256 values and the intake registry receipts. The original documents remain in ignored private storage.

This is the concrete ScriptaLocal-side agreement for the application coding agent. It preserves the existing Django/TypeScript interface, Codex provider, application worker authorization, separate unreviewed drafts and immutable revision rules. It does not authorize or claim deployment of an application adapter.

## Team handover

This document is for the application coding team. The agent providing integration information may differ from the agent implementing later changes. Any implementing agent should read this response, the canonical API contract and the application repository instructions; preserve recorded decisions and return evidence for the acceptance cases. No previous chat context or persistent agent identity is assumed.

## Running service versus designed service

- **Running:** file intake at `http://10.10.100.209:8091`, with request-scoped upload tokens. The two-file handover was received successfully. This is not a transcription endpoint or an OCR credential issuer for the app.
- **Designed and schema-tested:** OCR API [OpenAPI](api/openapi.json), [behavior](api/README.md), and [examples](api/examples/). The OCR HTTP server and resident pipeline workers are not deployed yet.
- **Working prototype:** a real manually bounded cell goes through pinned Kraken and TrOCR offline on the GPU host, yielding three line crops and a v1 structured result. This is not a full-row API or accuracy acceptance.

Do not use port 8091 or the upload ticket as a substitute for an OCR API base URL/credential. The future controller must use separately configured service access. Keep the Local HTR feature disabled until its actual endpoint and tests pass.

## Canonical v1 names

Use the existing ScriptaLocal draft as the canonical service shape; adapt the app-facing names at the controller boundary. Do not implement both competing shapes or silently accept unknown fields. These differences from the handover's proposed fixtures are intentional and must be reflected in the app mock/adapter:

| Proposed in app handover | Canonical ScriptaLocal v1 |
| --- | --- |
| Multipart `metadata` JSON file part | Multipart `options` containing a JSON string (`JobOptions`); `image` is the PNG file part |
| `schema_version: "1.0"` | Response `api_version: "1"`; the URL is `/v1/…` |
| Job `state` | Job `status` |
| `cell_detection`, `line_extraction`, `finalizing` | `layout`, `segmentation`, `complete`; other phases are `queued`, `recognition` |
| Required numeric/null `progress` | No numeric progress field; show indeterminate progress and the stage |
| Requested `model` | `options.model_id`; actual model revision and weights hash are in provenance |
| Cell/line `index` | Consecutive zero-based `order`, plus opaque UUID `id` |
| Only `bbox` | Original-coordinate `polygon`, plus line `baseline`; derive a bounding box when the app needs one, preserving raw geometry |
| Top-level `image_sha256`, `coordinate_system` | `source.sha256`, encoded/oriented dimensions, EXIF mapping and `coordinate_space` |
| `timings_ms` | `stage_seconds`; retain units/scope and convert explicitly if needed |
| `result_available` | On `status=succeeded`, retrieve `/result`; do not infer success from missing data |
| Failed status `error` object | Failed `JobStatus.error_code`; HTTP failures use `ErrorResponse.error` |
| Required creation timestamp in 202 | `JobAccepted` supplies job ID/status/revision/replay; timestamps are obtained from job status |

`client_reference` belongs in `JobOptions`; the app must bind its durable operation to the returned `job_id` and must not require an undocumented result echo. The service computes source hash/dimensions from uploaded bytes. The app verifies returned source metadata against its submitted crop; declared-input-hash extensions are not currently part of `JobOptions`.

The status/result examples are fictional contract fixtures. They establish data shapes, not running endpoint behavior. Keep terminal failure, HTTP failure, incomplete result and expired/lost remote state distinct. Missing or inaccessible IDs return 404; not-ready result returns 409. Do not assume the proposed 410 retention behavior until the implemented retention policy is published.

## Crop and geometry agreement

The current app prepares a 1:1 PNG of the selected row, up to 4 MiB/16 million pixels, with polygon-exterior pixels white. That fits ScriptaLocal's designed configurable decoder defaults (25 MiB/40 million pixels). App limits should remain unchanged. The source frontend on GitHub also confirms this crop behavior; the handover warns that its working tree can contain newer uncommitted code.

For these PNGs, ScriptaLocal's original encoded crop frame is the app's submitted crop frame, with no implied page rotation. Pixel edges are top-left origin, x right/y down, exclusive right/bottom bounds. Preserve the 1:1 crop-to-scan offset from the app. The service returns geometry already mapped back from its processing frame; do not apply its internal transforms again.

A bbox can be derived as `{x:min_x,y:min_y,width:max_x-min_x,height:max_y-min_y}` from a validated polygon. Keep the full polygon/baseline in protected raw result storage. Do not let ScriptaLocal manufacture application layout/record/column IDs.

Corrected service dividers are `options.corrected_dividers: [{"top":[x,y],"bottom":[x,y]}]`. They use original encoded crop coordinates; logical top/bottom ordering is checked after EXIF normalization. Endpoints are not arbitrary multi-point polylines. An empty list explicitly requests one cell. Reject unsupported correction geometry rather than silently simplifying it.

## Reading and mapping agreement

Require explicit, verified service-cell-order → selected immutable leaf-column-ID mapping. Equal counts alone do not prove mapping. A changed/missing layout or ambiguous boundary keeps the result in mapping review. The first Local HTR adapter can require a suitable selected layout; retain Codex's existing no-layout passage workflow.

| Service cell state | App interpretation |
| --- | --- |
| `recognized` | `transcribed`, preserving LF and exact text, in a new unreviewed draft |
| `blank` | `blank_in_source`, empty string, retained position and no lines |
| `unreadable` | `illegible`; preserve raw null/line evidence separately, not a fabricated literal placeholder |
| `failed` | `not_transcribed` only after explicit partial-result review, with a visible failure reason |
| `needs-review` | Retain raw result and require explicit review; do not automatically turn uncertainty into transcribed/blank text |

ScriptaLocal permits partial line evidence. If any line text is unknown (`null`), aggregate cell text is null; do not flatten it by dropping unknown/empty lines. Preserve raw JSON and its digest before any app publication. `raw_text/raw_state` are separate from effective text/saved corrections. Do not normalize names, whitespace or abbreviations.

`review_required` and warnings must remain visible. Geometry ambiguity is a mapping/review problem, not permission to rerun inference automatically. The app may publish only the explicitly accepted interpretation as an attributed unreviewed candidate; it must never overwrite the comparison target or a human-corrected generated reading. Capture index/UUID → stable app part-key associations at first import and reuse them for recovery.

## Durable controller and cancellation

The proposed application-side durable controller, provider-filtered claims, dedicated worker identity and protected configuration are appropriate. No GPU/Pillow dependencies belong on the app VM. Do not hold app transactions over HTTP. Persist upload intent, stable idempotency key, remote ID, pinned configuration, raw result digest and exact normalized publication data before acknowledging transitions.

The service queue under implementation is PostgreSQL-backed with leased/fenced stages and bounded retries. Idempotency keys are scoped by caller; the implementation must document their retention and restart behavior before release. Until that verification exists, do not claim the lost-202/restart acceptance case passes.

For the current designed API, cancellation returns a `JobStatus` envelope. Queued jobs can cancel immediately; running jobs expose `cancel_requested` until acknowledged/recovered. Repeated cancellation of a cancelled job is harmless. Already succeeded/failed jobs return 409 rather than rewriting history. The app must independently fence late imports after its cancellation; preserve a draft that had already committed before the app cancellation request.

A 900-second end-to-end app deadline is a reasonable configurable initial controller policy, not a measured performance guarantee. Persist it across restart. Terminal failure requires explicit user retry; no new remote idempotency key or cloud/Codex fallback is implicit. Authentication remains server-side with verified transport, configured URL, bounded reads and no credential-carrying redirects. The intake's owner-selected HTTP transport is not a declaration that the future OCR credential transport is already settled.

## Remaining inputs and acceptance

The received fixture README lists PNG/JSON fixtures, but those files were not part of the two-file upload. The two proposed fixture paths checked on GitHub `main` returned 404, consistent with the handover's uncommitted-working-tree warning. Consequently their exact bytes/schema cannot be claimed validated here. Use the canonical ScriptaLocal examples to build the mock; transfer the referenced synthetic fixture bundle or make the corresponding source available before cross-app fixture acceptance.

The full app adapter, provider migration/identity binding, raw-result/mapping storage, UI review states, actual worker-identity network/credential check, stale-edit/once-only publication and complete round trip remain unverified. The original handover's acceptance list remains required. Do not submit production crops or write research records just to demonstrate a mock connection.
