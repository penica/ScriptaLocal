# OCR API prototype validation

The ASGI factory `scriptalocal.ocr_api.create_app` implements authenticated upload, polling, result retrieval, model inventory, health, cancellation, paginated artifact inventory, verified original-image downloads and controlled derived artifact retrieval and deletion. This is a tested prototype, not a production OCR endpoint. Port 8091 continues to serve file handover only.

On 12 November 2026, 30 API integration tests passed against the isolated PostgreSQL validation database. Tests use synthetic images, seeded readiness markers and synthetic stage completion; they do not demonstrate an HTTP request reaching a resident GPU worker. Separate queue and worker regression runs passed 21 and 16 tests respectively. Private execution logs are under `data/validation/`.

The API validation container uses `deploy/api-validation/Dockerfile`, with 24 locked packages. Tested image ID: `sha256:4b8abb6e48917aacef416fefdf34475f9333f128571aa16e7e16ae80663ecac3`. Tests mount current source, integration tests and contract examples read-only over the image copies. The database is on an internal Docker network; the initial integration suite published no host port. A subsequent temporary Uvicorn process was bound only to host loopback at port 8092.

Coverage includes owner isolation, capability checks, expired/revoked credentials, revocation during a slow upload, bounded multipart streaming, MIME/pixel/byte limits, queue capacity, canonical idempotency, replay after configuration changes, deletion-versus-replay races, cancellation acknowledgement, artifact integrity, shared-source preservation, interrupted cleanup recovery and redacted error responses. An unexpected backend exception is contained without reraising its private details through the ASGI test client.

Deletion first commits a durable cleanup manifest and removes the accessible job metadata. HTTP 204 acknowledges this logical deletion. Filesystem cleanup can be deferred or fail; it is replayable and bounded. All source publishers and collectors must share the same source store, hash locks and database. A pre-test queue dump was saved privately; it has not been restore-validated and is not an independent backup.

Readiness currently returns HTTP 200 with a `ready` boolean; callers must inspect that boolean. A fresh matching model marker is required, and the subsequent resident worker now implements marker publication; see [resident HTTP evidence](resident-worker-validation.md). Readiness now compares the complete migration history and checksums against the installed source, checks required queue tables, and reports cleanup unavailable when a durable deletion has exhausted its retries. Readiness never applies migrations.

Remaining acceptance work includes production deployment configuration, multi-worker topology, background cleanup and retention, overall job deadlines, production mounts/network policy and Registers integration. HTR-017 remains open.

## File-configured HTTP bootstrap

`python -m uvicorn scriptalocal.ocr_server:application --factory` loads the absolute configuration path in `SCRIPTALOCAL_CONFIG`. Required keys are `dsn_file`, `originals_root`, `artifacts_root`, `credentials_file` and `profiles_file`, all absolute paths. Optional settings are `api` (including nested `image` limits), `queue`, `maximum_artifact_bytes` and `maximum_ready_age_seconds`. Unknown configuration keys are rejected. Database credentials are read from a bounded regular file without following its final symlink; factory failures expose a generic message. Credentials, profiles and state stay outside Git.

A real loopback HTTP probe passed process health (200), missing-auth rejection (401), authenticated readiness reporting no model, and manuscript upload rejection (503) without publishing the source. Logs contained neither the caller token nor sampled image bytes. This proves startup and fail-closed HTTP behavior, not GPU execution. See `docs/validation/ocr-http-bootstrap.json`. Docker did not publish the port when the API had only the internal database network; the HTTP probe added a dedicated bridge for the API while leaving PostgreSQL exclusively on its private network.

The same source also passed 65 core tests and 11 synthetic pipeline integration tests after the deletion migration was introduced. These runs preceded the later read-only schema-check helper; the 30 API tests cover that helper against the actual isolated database.

Artifact inventory and original downloads now pass three additional actual-database API cases, including pagination, job/owner scoping, checksum verification, bounds and corrupt/symlink/FIFO rejection. A separate restored database served 102 derived artifacts and three job-scoped original downloads through loopback HTTP, all with matching bytes and hashes. See `docs/validation/artifact-access.json` and `docs/backup-restore-validation.md`.

Correction routes and explicit historical-result queries now pass 30 API tests and real HTTP/GPU/database-restart validation. See [correction revisions](revision-validation.md). The earlier 28-test artifact report is a dated evidence snapshot.
