# Full-row leased pipeline validation

ImagePipeline now connects the existing OpenCV layout, Kraken segmentation, TrOCR recognition and PostgreSQL stage runner. This implements the complete image-to-structured-result processing path, with retained evidence. The OCR HTTP service and a deployed resident daemon remain open tasks.

## Processing and provenance

The layout handler reads only the hash-named original, verifies checksum and decoded source metadata, applies automatic or explicitly corrected dividers, and publishes a configuration-bound layout artifact. Invalid/no-cell layouts fail explicitly rather than fabricating a single cell. The configuration manifest binds source-file hashes, all runtime package versions, immutable worker image, model references, precision, processing settings and affine convention. That manifest is retained in the committed layout output and its SHA is included in final result provenance.

The segmentation handler reads only committed predecessor records for that job/revision, preserves context/ink, maps geometry into original coordinates, and stores each assigned/unassigned line PNG separately. Raw and source-clipped polygons, cell/canvas transforms and unassigned evidence remain in private intermediate metadata. A cell failure has its own warning and cannot become a blank reading.

The recognition handler accepts only the preceding stage's committed line artifact IDs, checks PNG bytes/pixel bounds, runs bounded single-line batches with cancellation checkpoints, and assembles exact LF/null text and own-scope warnings. It preserves blank-cell positions and distinct failed/needs-review states. It does not silently substitute a model or trim JSON/text to fit limits.

Pipeline settings configure image/layout/segmentation/recognition limits plus global caps: currently 257 cells, 2,048 total line artifacts, 32M line pixels and 2 MiB final JSON. These are tested worker defaults, not a claim that the future public API/app size agreement is finalized. Oversized work fails with result_limits_exceeded; no partial accepted JSON is manufactured.

## Synthetic integration checks

Eleven actual PostgreSQL tests use synthetic image strokes and injected model substitutes to isolate stage behavior. They cover multiline/blank-middle assembly, EXIF mapping, no-cell layouts, truncation/raw preservation, OOM and segmentation partial failures, source tampering, unavailable model selection, foreign artifact references and explicit global-size rejection. All pass; the separate core suite still passes 65 tests. Synthetic model output is not handwriting evidence.

The worker validation image adds PostgreSQL/Pydantic dependencies to the tested GPU runtime. All 171 installed versions exactly match its constraint snapshot and pip check passes. The original typing-extensions 4.14 constraint conflicted with typing-inspection 0.4.5; the worker lock explicitly uses 4.16.0, already exercised in CPU/queue environments. No host driver, Docker daemon or unrelated service was changed.

## Actual GPU full-row runs

One supplied private 2418×272 manuscript row went through all three actual adapters on host RTX 3060 device 0. Results:

| Measurement | First run | Short-lease verification |
| --- | --- | --- |
| Cells / assigned line readings | 18 / 24 | 18 / 24 |
| Stage attempts | One each | One each |
| Lease interval | 60 seconds | 10 seconds |
| Segmentation time | 56.610 s | 58.119 s |
| Segmentation heartbeat calls | Not instrumented | 19 |
| Total including model loading | 76.999 s | 78.640 s |

Text, geometry, cell IDs and provenance are identical across the two runs. The shorter lease test specifically proves actual inference can exceed its original lease while heartbeats preserve ownership; it is not a duplicate throughput benchmark. The first run used 1,756,423,168 bytes peak allocated CUDA memory, committed 34 artifacts and produced a 34,203-byte validated result. Original uploaded bytes remained unchanged. Models loaded once per process and were reused across the row's cells/line batches.

Every cell requires review. Automatic-layout warnings, missed/ambiguous line evidence, paper/rule artifacts and crop quality remain unresolved quality issues; 24 is a detected count, not an annotated true line count. No CER/WER or production acceptance follows from successful processing. Owner-authorized assistant visual checks subsequently identified ruling-only detections and a crop-sensitive clear-word case; [candidate review](model-candidate-review.md) records these diagnostics without publishing manuscript readings.

The containers ran nonroot with read-only source/models/root, explicit resource caps, offline model settings and only the dedicated internal database network. That Docker network was verified internal; no external route/model download or cloud fallback was used. Raw results/crops remain under ignored private `data/validation/queued-full-row*`. Public metadata is in `docs/validation/queued-full-row*.json` and `pipeline-integration.json`.

## Reproduction

Build `deploy/worker-validation/Dockerfile`. The host's private `data/validation/pipeline.env` supplies immutable image ID and configurable project/model/reference/artifact/database roots. The isolated jobs-validation database must already be healthy. Do not run its destructive synthetic tests concurrently with a GPU probe using that same validation database.

```bash
docker compose --env-file data/validation/pipeline.env \
  -p scriptalocal-pipeline-validation -f deploy/worker-validation/compose.yaml \
  --profile gpu config --quiet
# Synthetic integration only; no GPU requested:
docker compose --env-file data/validation/pipeline.env \
  -p scriptalocal-pipeline-validation -f deploy/worker-validation/compose.yaml run --rm tests
```

The GPU profile runs the full-row probe. Give each probe a distinct writable output root; it refuses to start when earlier validation jobs are active and records a private job receipt before processing. Use --lease-seconds 10 with `scripts.validation.pipeline_probe` for short-lease verification. This is not a production deployment command.

Remaining work includes robust line rendering/crop quality, broader annotated samples and comparative accuracy, resident worker service/readiness, API authentication/routes/revisions/deletion, backup restoration, two-GPU throughput/failure acceptance and the Registers/Builder application round trip.
