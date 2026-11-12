# Builder: implement the Registers Local HTR provider

ScriptaLocal is running. Implement and test the Registers adapter now, following that repository's instructions and deployment rules. Registers is the integration contact; Builder owns application changes. This document supersedes earlier statements that the service is unimplemented. Model/training accuracy is outside the current integration gate.

## Connection

Origin: `https://scripta.penic.net:8093`. Use normal system CA and hostname verification: the certificate is issued by Let's Encrypt. Keep bearer credentials on the application backend. The separately shared `connection.json` contains a short-lived acceptance credential, isolated to the `registers-acceptance` job owner. It contains no Cloudflare or administrator credentials. Do not commit that file, log it, embed it in frontend assets, follow credential-carrying redirects or silently use another provider.

First run, from the actual application worker identity: authenticated `GET /health/ready` must return HTTP 200 **and** `ready:true`; then `GET /v1/models`. Report certificate/DNS/routing/authentication failures distinctly. Port 8091 remains file handover, not OCR.

## Implement

1. Keep explicit Codex selection and add explicit Local HTR selection behind the application feature flag. Use a configured HTTPS origin and backend-only credential. Preserve existing crop limits (4 MiB/16M pixels) and 1:1 PNG/crop-to-scan mapping.
2. Send `POST /v1/jobs` with multipart `image` and an optional JSON **string** `options`. Persist a stable caller-scoped `Idempotency-Key`, upload intent and remote job ID. Same canonical input/key replays the existing operation across API restarts; different input returns 409. Never create a fresh key automatically after an ambiguous response.
3. Poll `GET /v1/jobs/{id}`. Status is queued/running/succeeded/failed/cancelled; running stages are layout/segmentation/recognition. Fetch `/result` only after success. Persist raw response bytes/digest before publication. Preserve unknown timing/provenance keys.
4. Validate result structure and semantic rules before import. Use the accepted 52-test revised fixture bundle and `api/semantic-validation.md`. Preserve ordered cells, blank middle cells, LF, null readings, raw/effective text, own-scope warnings and original-encoded geometry. Do not transform service coordinates a second time.
5. Require verified service-cell-order to immutable leaf-column-ID mapping; matching counts alone are insufficient. Retain mapping review when boundaries/layout are uncertain. Create an attributed **unreviewed draft** with review reasons; never overwrite human edits. Enforce the app's 50-entry/2,000-part/200,000-character limits before publication; never truncate to fit.
6. Persist cancellation/deadline intent and fence late imports. `POST /cancel` returns JobStatus; running cancellation needs acknowledgement, queued cancellation is immediate, repeated cancelled cancellation is harmless, succeeded/failed cancellation returns 409. Keep the proposed configurable 900-second app controller deadline; the service does not yet enforce a global job deadline. No automatic Codex/cloud fallback.
7. Surface actionable 401/403/409/413/415/422/429/503 errors. Bound reads (service result maximum 2 MiB), connection timeouts and polling. Retention is not automatic yet. `DELETE` acknowledges logical deletion with replayable filesystem cleanup; do not treat it as proof every physical copy/backup is already gone.

Service caps are currently 257 cells, 2,048 global line artifacts, 32M total line pixels and 2 MiB final result JSON. Decoder defaults are 25 MiB/40M pixels/20k side. App limits remain stricter. `GET /jobs/{id}/artifacts` pages committed outputs (limit 1–200, next_cursor → after) and includes a job-scoped original descriptor; downloads are under `/artifacts/{artifact_id}`. Text/geometry revisions are supported at `POST /revisions` with `expected_revision`; immutable history is `/result?revision=N`. Backend corrections may be integrated later if the app already keeps its own edits.

## Acceptance and response

Run unit/fixture tests, then a real nonpublishing upload/poll/result round trip from the application worker using a synthetic crop. Test lost-202/idempotent retry, blank/null/partial-review import, mapping mismatch, stale edits, cancellation/late-result fencing and credential/TLS failures. Use an isolated synthetic application workspace for publication acceptance; do not write production research records merely to test connectivity. Keep Local HTR disabled for production until the repository's integration/deployment gates pass.

Return: branch/commit, changed files, exact test results, actual worker-side TLS/readiness/models outcome, synthetic job ID/result digest, and any concrete remaining blocker. Register the dedicated identity and server-filter provider claims as agreed in the original Registers handover. Coordinate with Registers if another agent owns that application scope.

Scripta's backend HTTP/GPU, revisions, cancellation/lease recovery and restart tests already pass. A row takes approximately 68 seconds in the single measured persistent-service run; this is not a latency guarantee. External application integration remains unverified until Builder reports and we inspect its evidence.
