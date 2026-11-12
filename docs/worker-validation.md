# Leased stage worker validation

`StageWorker` connects synchronous stage handlers to the durable PostgreSQL queue. It is a reusable runner, not yet a deployed image-processing daemon or OCR endpoint. Actual layout/segmentation/recognition stage payload assembly remains the next implementation step.

A worker claims only its supported stages and, when configured, the exact pinned configuration SHA. It revalidates the lease before running, then maintains it with an independent heartbeat thread. Handlers receive a context with a lightweight cancellation/ownership checkpoint and access to committed predecessor artifacts. Lazy handler generators are consumed while heartbeats remain active, so inference cannot accidentally start after the heartbeat stops.

Recognition checks the context before/after bounded generation calls and before smaller OOM recovery batches. Segmentation checks it before inference and while consuming extracted lines. ProcessingInterrupted passes through both adapters without being rewritten as a recognition/segmentation error. A running native/GPU operation is not forcibly interrupted; it may finish, but late output cannot become a committed result after cancellation or lease loss.

On cancellation, the worker acknowledges only while still owning the lease. On stale ownership it abandons publication. Heartbeat/database uncertainty fails closed: no output references are committed and the worker becomes unhealthy, refusing subsequent claims. A supervisor must restart an unhealthy process rather than continue its claim loop. If an in-flight heartbeat cannot stop within its configured join timeout, the same rule applies; Python does not forcibly terminate that thread.

Completed records are published using the queue's attempt fencing and atomic metadata transaction. The runner revalidates before final publication; cancellation/expiry can still win at the final queue boundary. Invalid artifacts become explicit terminal errors. Explicit retryable stage failures use bounded queue attempts. Unexpected handler messages are reduced to public error codes, without storing/logging exception bodies. If a database response is lost after a successful commit, the worker reports unavailable and stops; recovery observes the one committed output/next stage and cannot publish the old stage twice.

## Stage inputs and database bounds

`input_artifacts(lease)` returns only the preceding succeeded stage's committed artifact descriptors for that same job and revision, while checking the active lease and cancellation. It accepts no caller path. Layout has no predecessor; segmentation consumes layout, recognition consumes segmentation. Reading an artifact still validates its generated path, regular-file identity, byte count and SHA. Unreferenced stale-attempt files are not stage inputs.

Queue settings now expose database connect/statement/lock/TCP-user/keepalive bounds, all positive validated values. Defaults are 5-second connection and statement bounds, 2-second lock bound, 5000-ms TCP user timeout, and keepalive idle/interval/count 5/2/2. The heartbeat interval defaults to min(10 seconds, lease/3), and may not exceed lease/3; the join bound is separately configurable. Server-side statement cancellation is verified against an actual pg_sleep query. Controlled thread/outage tests establish fail-closed runner behavior; they do not prove every physical network-failure latency.

## Evidence and reproduction

Sixteen tests use the existing isolated real PostgreSQL validation database. They exercise:

- Lazy work longer than its original one-second lease, without reclamation while heartbeats run.
- Running cancellation, stale ownership after replacement, and refusal to publish late artifacts.
- Startup/midstage heartbeat faults, an unresponsive heartbeat drain, and refusal to reuse unhealthy workers.
- Bounded retry, redacted unexpected failures, invalid JSON completion and unavailable cancellation acknowledgement.
- Configuration routing, committed predecessor scope, an ambiguous commit response with exactly one accepted output, and enforced statement timeout.

```bash
docker compose --env-file data/validation/jobs-database/compose.env \
  -p scriptalocal-jobs-validation -f deploy/jobs-validation/compose.yaml \
  --profile tests run --rm tests python -m unittest discover \
  -s integration_tests -p test_workers.py -v
```

The same queue's original 20 integration tests also pass after the query/connection changes. The complete core suite passes 65 checks, including cancellation propagation through the two adapters. Evidence: [worker report](validation/worker-orchestration.json), [integration output](validation/worker-tests.txt), [core output](validation/worker-core-tests.txt). The tests have explicit dedicated-database/role guards and modify synthetic queue tables only. Intake and unrelated host applications stayed running.

Remaining acceptance includes real stage schemas and image artifact assembly, resident model startup/readiness, full-row processing through these leases, actual GPU worker termination/restart, durable overall deadlines, HTTP routes/authentication/retention and application round trip. No queue-worker or manuscript accuracy release is implied by these generic-handler tests.
