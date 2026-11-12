# Durable stage queue validation

HTR-016 implements PostgreSQL job/revision/stage metadata and an immutable attempt-scoped artifact store. It is a library used by future HTTP/worker processes; no OCR HTTP service is deployed by this work.

The processing order is layout → segmentation → recognition. Claims use job locks with `SKIP LOCKED`, fresh attempt UUIDs and database-clock deadlines. Every heartbeat/failure/completion checks the current revision, worker, attempt and live lease. Admission is bounded atomically. Idempotency keys are caller-scoped, bind source/options/configuration and persist with the job. Until job deletion/retention is implemented, no key expiry is performed.

Artifacts are written and fsynced under generated job/revision/stage/attempt/UUID paths before a short database transaction references them. A failed or fenced attempt may leave unreferenced files; it cannot overwrite an accepted result. Completion publishes references and the next stage or final result together. Identical completion replays are harmless; different output for a completed attempt conflicts. Final JSON must satisfy the public result schema and match the job source, configuration and revision. No transaction is held over GPU inference.

Queued cancellation is immediate; a running job records a request which its worker acknowledges or expired-lease recovery applies. Completion and cancellation lock the same job to produce one consistent outcome. Terminal results remain immutable. Error metadata uses bounded public codes, not exception messages.

## Reproduction

Build the pinned eight-package runner:

```bash
docker build -f deploy/jobs-validation/Dockerfile -t scriptalocal-jobs-validation:locked .
docker compose --env-file data/validation/jobs-database/compose.env \
  -p scriptalocal-jobs-validation -f deploy/jobs-validation/compose.yaml \
  --profile tests run --rm tests
```

The host's private environment file configures project and dedicated validation storage roots and the runner image. It points only to generated test secrets; no credentials are in this repository. A new setup must generate a dedicated password and DSN file, set their permissions to 0600, initialize PostgreSQL storage with its container UID, and provide a writable artifact folder for UID 1000. Database name/role must be `scriptalocal_queue_validation`/`scriptalocal_test`: tests refuse other identities and explicitly opt into destructive synthetic-table cleanup. Never target application or unrelated PostgreSQL databases.

The database image is digest-pinned PostgreSQL 17.11, uses an internal Compose network and publishes no host port. The runner runs unprivileged with a read-only root, bounded tmpfs/resources, dropped capabilities and no privilege escalation. Source and DSN mount read-only; only synthetic artifacts are writable.

Twenty tests cover real concurrent claims/submissions, queue admission, scope, migration checksum rejection, lease heartbeats/expiry, bounded crash/retry handling, cancellation including a completion race, immutable artifact boundaries and exactly one final result publication. Evidence: [queue report](validation/jobs-queue.json), [test output](validation/jobs-tests.txt).

The separate restart probe prepared one synthetic running job, expired its lease and wrote an attempt artifact. Only the verified dedicated database container was killed with SIGKILL and restarted. PostgreSQL logs confirmed automatic WAL recovery. Probe verification replayed submission, rejected old worker completion, reclaimed the lease, published one new output and left the original attempt bytes unchanged and unreferenced. The probe cancelled its next queued stage. Evidence: [restart report](validation/jobs-restart.json). Do not rerun `verify` against an already consumed probe state; it intentionally changes queue state.

Remaining service-level work includes worker integration, public request authentication, status conversion, deletion/retention and orphan collection, new correction revisions, backup restoration and full image-to-result acceptance. Queue tests do not establish manuscript accuracy or external application integration.
