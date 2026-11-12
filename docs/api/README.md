# ScriptaLocal HTTP contract, version 1

Status: designed and schema-tested contract. HTTP routes are not implemented or deployed yet. [openapi.json](openapi.json) is the machine-readable definition. [examples/](examples/) contains fictional fixtures, never manuscript content. The definitions live in `scriptalocal/contracts.py`; regenerate the specification using `python -m scripts.export_contract` in the CPU validation environment.

## Upload and lifecycle

The caller uploads one image using multipart field `image`. Optional multipart field `options` is a JSON string conforming to `JobOptions`. Send actual PNG/JPEG/single-image TIFF bytes, not a filename, local path, base64 wrapper, remote URL or paragraph transcription request. `client_reference` is an opaque caller reference, not a path or authorization mechanism. Omitted model selection uses server configuration; models are provisioned separately and never downloaded by an upload request.

The intended default decoder limits are 25 MiB, 40 million pixels and 20,000 pixels per side. They are constructor/configuration values, not fixed application assumptions. Upload handlers must enforce the byte limit while reading, before handing the bounded payload to `decode_image`. Unsupported formats, MIME mismatches and multiple frames return 415; byte/pixel/side limits return 413; corrupt files return 422. Transparent images are composited on white for recognition; original bytes are retained unchanged.

Initial upload returns 202 `JobAccepted`. Poll `GET /v1/jobs/{job_id}` until a terminal status. `queued → running → succeeded`, `failed` or `cancelled` are the lifecycle states. A `succeeded` job means a complete structured result exists; its cells may still need human review. A failed job carries a public error code, never an exception traceback. `stage=complete` applies only to succeeded jobs. No fabricated percentage progress is exposed.

Retrieve the result only after success. A result that is not ready returns 409. The current design exposes the latest revision; callers must compare its revision before importing. Older raw results and human readings remain in service storage and must never be overwritten by worker retries. Persisted history retrieval can be added as a versioned endpoint if the caller needs it.

`Idempotency-Key` is optional, printable ASCII, 1–128 characters. The server scopes it to the authenticated caller. Reusing a key with the same image and canonical options returns the same job ID with `idempotency_replayed=true` and its current status. Different content returns 409. The key reservation must be atomic with job creation, not an in-memory cache. The retention window must be documented when implemented. Queue pressure returns 429 with a retry policy; missing models/components return 503 rather than cloud fallback.

Cancellation is best effort: a running job first reports `cancel_requested=true`, then becomes cancelled when its worker acknowledges it; queued jobs can be cancelled immediately. Repeating cancellation of an already cancelled job is harmless. A succeeded or failed job cannot be retroactively cancelled; return 409. Delete requires delete capability and coordinated database/artifact cleanup. A running job must be cancelled and acknowledged before deletion; return 409 otherwise. Missing or inaccessible IDs return the same 404 response.

## Coordinates and result ordering

Every cell polygon, line polygon and baseline is expressed in **original encoded raster pixel-edge coordinates**: origin at top left, x increases right, y increases down, x in `[0,width]`, y in `[0,height]`. Pixel centers are `(x+0.5,y+0.5)`. This convention avoids half-pixel ambiguity for crop boundaries.

`source.width/height` describe the encoded raster before EXIF orientation; `oriented_width/height` describe the recognition-oriented image. `source.original_to_oriented` is six affine coefficients `[a,b,c,d,e,f]` with `x'=a*x+b*y+c`, `y'=d*x+e*y+f`. It must match EXIF orientation. Additional reversible crop/deskew/padding transforms use the same coefficient convention in `transforms`, with explicit source and destination space names. Geometry returned to the caller must already be mapped back; the caller must not rotate it a second time.

Cell arrays are left-to-right in recognition orientation. Line arrays use top-to-bottom reading order within each cell. `order` is zero-based and consecutive; IDs are stable opaque UUIDs independent of array indexes and the caller's domain IDs. Preserve blank cells in place. Geometry resegmentation may create different cell/line IDs; do not silently map them onto old IDs or old manual corrections.

## Reading states and human edits

| State | Meaning | Text |
| --- | --- | --- |
| recognized | Nonempty recognition or a saved human reading | Verbatim string |
| blank | Explicitly identified blank cell | Empty string, no detected lines |
| unreadable | Human/validated policy identifies unreadability | null |
| failed | A processing stage failed | null |
| needs-review | Layout, segmentation or recognition is uncertain | Partial string or null; reason required |

Do not infer a blank cell from an empty recognizer output or failed segmentation. A cell with no lines and uncertain ink has `text=null`, a review reason and `review_required=true`. For cells with lines, text is the newline join of effective line text; if any line has unknown text, the aggregate text is null and the individual lines retain their partial readings.

`LineResult.raw_text/raw_state` preserve machine output. `text/state` are the effective reading, equal to the raw output unless a saved `correction` exists. Corrections carry server-recorded actor and timestamp metadata. Raw results remain immutable across corrections. `recognition_model` records the immutable model revision and weights hash; no confidence percentage is invented.

Submit corrections with `POST /v1/jobs/{job_id}/revisions` and `expected_revision`. Stale revisions return 409. A request contains text corrections for existing line IDs **or** new divider geometry. Geometry and old-line text edits cannot be combined because resegmentation invalidates those IDs. Actor metadata is derived from authentication, not accepted from the request. Accepted changes create a new revision and return 202; geometry changes queue downstream stages, while text-only changes queue result assembly. An empty divider list explicitly requests a single cell. Geometry bounds, border crossings and accessibility of target IDs must be validated against the job before acceptance. Divider `top` and `bottom` identify logical endpoints in the oriented view, but their coordinates are stored in the original encoded frame. A 90-degree EXIF rotation can therefore make their original y values equal. The worker transforms them once into the oriented frame before checking top-to-bottom order; it must not require increasing encoded y.

## Access and integration

Service tokens use bearer authentication with server-assigned read/write/delete capabilities. Liveness may be unauthenticated; readiness, model inventory, jobs and artifacts require appropriate capability and caller ownership. These are implementation requirements, not evidence of working authentication. Keep tokens on the existing application's backend. Default service binding is loopback; remote/LAN use needs configured routing and HTTPS. No CORS permission is assumed.

Artifact URLs accept an opaque artifact UUID owned by the job. They never accept paths or user-supplied filenames. Error bodies use `ErrorResponse` with a public code/message, retryable flag and request UUID. Routine logs must not contain uploaded image bytes, credentials, raw text, corrections or arbitrary decoder exception details.

The API design must be checked against the existing app handover before caller implementation. This contract does not claim compatibility with an app whose provider interface is not yet supplied.

## Validation

`tests/test_contracts.py` checks fixtures and invariants including blank middle cells, line breaks, unique IDs, state/text consistency, geometry bounds, EXIF dimensions/mapping, uncertainty, immutable raw output, saved corrections, revision conflicts and schema reference resolution. `tests/test_images.py` checks actual decoding and coordinate/pixel agreement, original publication and invalid input handling. A schema test does not establish HTTP routing, persistence, authentication or handwriting accuracy; those remain service acceptance tasks.

Implementation references: [Pillow image verification](https://pillow.readthedocs.io/en/stable/reference/Image.html) and [Pydantic model validation](https://docs.pydantic.dev/latest/concepts/models/). Runtime behavior was tested against the pinned versions in the CPU validation image.
