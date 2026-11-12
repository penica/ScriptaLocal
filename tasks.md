# Implementation tasks

Plan: [docs/implementation-plan.md](docs/implementation-plan.md). Agent rules: [AGENTS.md](AGENTS.md).

Updated: 12 November 2026. Validation is in progress. Only tasks with recorded completion evidence are checked.

Use TODO, DOING or BLOCKED with an unchecked box; use DONE with a checked box only after the stated acceptance evidence exists. Add dated Evidence while working and dated Completion when finished. IDs are stable. Owners identify the chat or implementer currently responsible; unassigned work is not implicitly running. HTR-027 is optional and is not a release dependency.

- [x] **HTR-001 — Inspect host and select deployment architecture** — DONE
  - Owner: current chat.
  - Done when: Host GPU, CPU, RAM, storage and runtime inventory supports a documented Docker/VM/LXC decision.
  - Completion, 12 November 2026: Read-only nvidia-smi, lscpu, memory/disk, OS, Docker/Compose/runtime and block-device inspections identified two RTX 3060 12 GB cards, AVX without AVX2, working Docker daemon queries and about 13 GiB root free space. Selected host Docker Compose with explicit GPU workers. Container GPU computation and ZFS availability remain unverified and are tracked separately.

- [x] **HTR-002 — Write implementation plan and agent guidance** — DONE
  - Owner: current chat.
  - Depends on: HTR-001.
  - Done when: AGENTS.md, full implementation plan and actionable task checklist cover architecture, table/line recognition, API, GPU strategy, quality gates, integration and operations.
  - Completion, 12 November 2026: Created all three documents with source links, deployment rationale, measured host constraints, staged rollout, acceptance criteria and task dependencies. Verified local document links, unique task IDs, dependency resolution and checkbox/status consistency. No application installation or model benchmark claimed.

- [x] **HTR-003 — Initialize repository and development conventions** — DONE
  - Owner: current chat.
  - Depends on: HTR-001, HTR-002.
  - Done when: Create Git repository and ignores for scans, secrets, weights, database and generated files; add configuration example and developer commands without committing local data.
  - Completion, 12 November 2026: Repository already tracks origin/main; added .env.example, Makefile commands, .dockerignore and existing Git ignores for private data/models/secrets. git check-ignore confirms reference images, models and .env are excluded. Validation implementation files are in this worktree.


- [x] **HTR-004 — Choose safe storage and capacity budget** — DONE
  - Owner: current chat.
  - Depends on: HTR-001.
  - Done when: Inspect Docker data root, mounted filesystems and ZFS dataset ownership/capacity; select explicit build, model, artifact and database paths with at least 60 GiB initial free allowance or a measured revised budget. Document backup destination. Do not delete unrelated data or repurpose disks.
  - Completion, 12 November 2026: Confirmed 179 GiB root free before downloads, inspected Docker root and ZFS ownership/capacity, and chose configurable project data/{models,artifacts,postgres,build-cache,reference} paths. User selected /srv/storage/scriptalocal-backups pending NAS. All data paths passed write/fsync/atomic rename/read checks; a 64 KiB backup fixture restored with matching SHA-256. ZFS member read errors and the absence of off-machine backup are recorded; full database restore remains HTR-025. Evidence: docs/validation/{host-inventory,storage}.json.


- [x] **HTR-005 — Validate GPU access from containers** — DONE
  - Owner: current chat.
  - Depends on: HTR-004.
  - Done when: Inspect existing NVIDIA runtime/CDI configuration, provision only missing components, and verify both GPU UUIDs inside a pinned test container. Run actual CUDA computation separately on each card; record versions and rollback for any host configuration change.
  - Completion, 12 November 2026: Pinned PyTorch base image digest and used existing NVIDIA CDI mapping without host changes or Docker restart. Each RTX 3060 passed CPU/GPU matrix comparison and CUDA convolution in a network-disabled container, with exactly one visible GPU and distinct UUIDs. Evidence: docs/validation/gpu-{0,1}-runtime.json and repeatable probe commands.


- [x] **HTR-006 — Validate CPU and CUDA dependency compatibility** — DONE
  - Owner: current chat.
  - Depends on: HTR-005.
  - Done when: Pin and test Python, PyTorch, Transformers, Kraken, NumPy and OpenCV combinations on the AVX-only Xeon. Verify imports, CPU geometry operations and GPU inference dependencies; split worker environments where needed. Record image digests and locks.
  - Completion, 12 November 2026: Built and rebuilt pinned Python 3.11.13/PyTorch 2.7.1+cu126 environment with a complete 165-package version constraint snapshot. Rebuilt package list exactly matches the lock, pip check passed, and the rebuilt image passed CPU OpenCV/NumPy/SciPy/scikit-image, Kraken binarization/imports and CUDA TrOCR architecture generation. Actual Bern safetensors model also loaded and generated offline on GPU 1. Initial Compose read-only/no-network GPU 0 probe passed. Evidence: deploy/validation/constraints.lock and docs/validation/dependencies-locked-gpu-0.json; learned segmentation quality and real manuscript accuracy are separate open validations.


- [ ] **HTR-007 — Define API schema and result semantics** — TODO
  - Owner: unassigned.
  - Depends on: HTR-002.
  - Done when: Publish versioned OpenAPI/JSON examples for jobs, ordered cells/lines, original-image coordinates, transforms, states, provenance, errors, corrections and idempotency. Specify limits and distinguish blank, unreadable, failed and needs-review.

- [ ] **HTR-008 — Assemble manuscript reference corpus** — DOING
  - Owner: current chat.
  - Depends on: HTR-002.
  - Done when: Obtain representative user crops and permissions; annotate at least 30 rows and 200 lines where available, including faint/broken/slanted borders, empty cells, crossing strokes and mixed scripts. Reserve page/hand-separated held-out data; document any shortage and retain synthetic fixtures separately.
  - Evidence, 12 November 2026: Created ignored data/reference/row-crops/ and data/reference/ground-truth/ folders. User will supply images. No manuscript crop or ground truth exists there yet; handwriting accuracy and held-out evaluation remain unverified.
  - Evidence, 12 November 2026 (sample received): reference-crop.jpg uploaded, 2418 × 272 pixels. First real single-cell segmentation/recognition probe is running. No human ground truth or larger page/hand-separated corpus yet.


- [ ] **HTR-009 — Provision reproducible offline models** — DOING
  - Owner: current chat.
  - Depends on: HTR-004, HTR-006.
  - Done when: Pin Bern recognition and candidate Kraken segmentation model revisions, hashes, licenses and configuration. Verify safe loading, cache completeness and offline mode. Document actual disk use; select segmentation weights using sample evidence.
  - Evidence, 12 November 2026: Provisioned only inference files for Bern model commit a6bb6086ba3f7ddc62193a0289b1ee733c1af15e; verified upstream safetensors SHA-256 and recorded all file hashes/MIT model-card license. Real FP32 inference passed offline on GPU 1 using a synthetic printed line (not handwriting accuracy). Bundled Kraken segmentation candidate is being exercised; sample-based model selection remains open.
  - Evidence, 12 November 2026 (learned segmentation): Bundled blla candidate SHA-256 77a638a83c9e535620827a09e410ed36391e9e8e8126d5796a0f15b978186056 ran offline. Default scaling missed all three lines in the real cell; padding to preserve pixel scale found three lines, visually checked via saved crops, and real TrOCR generation ran on each. This is one-sample functional evidence, not model-selection/accuracy acceptance.


- [ ] **HTR-010 — Build single-cell inference vertical slice** — TODO
  - Owner: unassigned.
  - Depends on: HTR-006, HTR-007, HTR-009.
  - Done when: Run a real manually bounded multiline cell through Kraken line extraction and TrOCR single-line recognition on GPU 0; return ordered text and geometry with model provenance. Record time and VRAM. A sample is required for handwriting acceptance.
  - Evidence, 12 November 2026: scripts/validation/manuscript_slice.py runs the supplied manuscript with explicit manual cell bounds through learned Kraken segmentation and real TrOCR offline on GPU 0, preserving geometry and private line crops/text. Padded candidate extracted three visible lines; summary timings/VRAM in docs/validation/manuscript-cell-padded-summary.json. The unpadded failure is retained as needs-review evidence. Versioned service contract/provenance integration remains open, so task is unchecked.


- [ ] **HTR-011 — Implement image validation and geometry transforms** — TODO
  - Owner: unassigned.
  - Depends on: HTR-007.
  - Done when: Preserve original image bytes and hashes; validate dimensions, format, orientation and decoding limits. Support PNG/JPEG/single-image TIFF and reject unsupported multi-frame inputs. Verify coordinate round trips and corrupt/oversized image handling.

- [ ] **HTR-012 — Detect vertical borders and derive cells** — TODO
  - Owner: unassigned.
  - Depends on: HTR-008, HTR-011.
  - Done when: Implement conservative morphological and line-evidence detection with duplicate merging, skew tolerance and image-edge bounds. Verify cell count/order on reference fixtures, faint and broken borders, handwriting strokes, double borders and no-divider inputs; return ambiguity diagnostics.
  - Evidence, 12 November 2026: Added configurable row_geometry_probe.py and generated a private divider overlay/cell crops on the uploaded row. Removed outside-table edge strips from candidate cells after visual review. This is an exploratory single-image probe, not the implemented/accepted detector or full difficult-border benchmark.


- [ ] **HTR-013 — Preserve border-crossing ink and correct layouts** — TODO
  - Owner: unassigned.
  - Depends on: HTR-012.
  - Done when: Keep original pixels and padded recognition crops; make any border masking reversible. Accept validated divider corrections and uncertain/merged-cell states; preserve empty cells and produce overlay diagnostics without silently assigning a fixed schema.

- [ ] **HTR-014 — Implement robust per-cell line segmentation** — TODO
  - Owner: unassigned.
  - Depends on: HTR-010, HTR-013.
  - Done when: Use pinned Kraken segmentation with line extraction, baseline ordering and original-coordinate mapping. Verify multiline, overlapping, blank and long-line cases; expose segmentation failure and review reasons instead of successful empty output.

- [ ] **HTR-015 — Implement resident recognition worker** — TODO
  - Owner: unassigned.
  - Depends on: HTR-010, HTR-014.
  - Done when: Keep TrOCR loaded, batch bounded single-line images, preserve verbatim output and detect generation truncation. Compare FP32/FP16, handle OOM with bounded recovery, record model revision and avoid uncalibrated confidence claims.

- [ ] **HTR-016 — Add persistent jobs and leased stage queue** — TODO
  - Owner: unassigned.
  - Depends on: HTR-007, HTR-011.
  - Done when: Create PostgreSQL migrations, atomic stage claims, heartbeats, lease recovery, idempotency and atomic artifact publication. Verify concurrent claims, worker crash recovery, duplicate submissions, cancellation and bounded retries without duplicate final revisions.

- [ ] **HTR-017 — Expose asynchronous transcription API** — TODO
  - Owner: unassigned.
  - Depends on: HTR-015, HTR-016.
  - Done when: Implement validated upload, status, result, model inventory, health, controlled artifact retrieval, cancellation and deletion. Verify consistent error responses, pixel/upload/queue limits and stable ordered cell/line results end to end.

- [ ] **HTR-018 — Record corrections and result revisions** — TODO
  - Owner: unassigned.
  - Depends on: HTR-013, HTR-017.
  - Done when: Store raw recognition separately from human edits, preserve provenance and optimistic concurrency, and regenerate affected stages after geometry edits. Verify conflicting updates and cancellation cannot overwrite completed correction history.

- [ ] **HTR-019 — Package Compose deployment and local access** — TODO
  - Owner: unassigned.
  - Depends on: HTR-005, HTR-006, HTR-017.
  - Done when: Provide pinned images, health checks, resource caps, internal-only database/workers, persistent mounts, secrets example and loopback API binding. Start/restart the stack on this host and document commands and measured disk use.

- [ ] **HTR-020 — Verify privacy, input boundaries and LAN configuration** — TODO
  - Owner: unassigned.
  - Depends on: HTR-019.
  - Done when: Verify offline inference after provisioning, no silent cloud fallback, redacted logs, controlled paths, decoder limits and protected credentials. Provide opt-in LAN routing with scoped authentication, HTTPS and narrow CORS; keep default loopback deployment working.

- [ ] **HTR-021 — Provide caller client and provider integration** — TODO
  - Owner: unassigned.
  - Depends on: HTR-007, HTR-017, HTR-020.
  - Done when: Ship a usable upload/poll/export client. Once existing-app source or provider contract is supplied, add Local HTR selection, service configuration, progress, actionable errors and cell-preserving result import; test a real round trip while retaining explicit Codex choice. Record external integration as incomplete until tested.
  - Evidence, 12 November 2026: Created docs/existing-app-handover.md with a ready-to-send request for the other AI coder covering crop/provider/result contracts, networking, revisions and sanitized fixtures. Existing-app contract/source is not supplied yet.


- [ ] **HTR-022 — Benchmark recognition and layout quality** — TODO
  - Owner: unassigned.
  - Depends on: HTR-008, HTR-015, HTR-017.
  - Done when: Compare pinned TrOCR candidates and a suitable licensed Kraken recognition model using identical held-out samples. Report layout errors, CER/WER, names/dates, blank false positives and review rate by script/hand. Separate manual-layout and automatic-layout results; agree numeric acceptance criteria from evidence.

- [ ] **HTR-023 — Enable and benchmark second GPU worker** — TODO
  - Owner: unassigned.
  - Depends on: HTR-019, HTR-022.
  - Done when: Assign devices explicitly and compare one versus two independent recognition workers, or GPU segmentation if CPU is limiting. Record throughput, p50/p95 latency, VRAM, RAM and ordering; demonstrate no duplicate work or unintended GPU access. Select measured default topology.

- [ ] **HTR-024 — Add retention, monitoring and recovery controls** — TODO
  - Owner: unassigned.
  - Depends on: HTR-016, HTR-019.
  - Done when: Expose stage/queue/worker timing and disk health without manuscript text. Bound logs, caches and retries; implement configurable artifact retention with protection for corrected reference material. Verify stale leases, full disk, missing models, worker loss and GPU OOM produce recoverable explicit outcomes.

- [ ] **HTR-025 — Verify backup restoration and deployment rollback** — TODO
  - Owner: unassigned.
  - Depends on: HTR-018, HTR-024.
  - Done when: Back up PostgreSQL plus consistent artifact manifests and corrections to independent storage. Restore in isolation and verify image hashes, jobs and revision history. Demonstrate image/config rollback and a safe migration restore procedure.
  - Evidence, 12 November 2026: User chose local storage for initial backups and future Synology NFS. Configurable local destination created with mode 0700. A temporary 64 KiB file restored with matching SHA-256; this is not application/database restoration and does not complete the task.


- [ ] **HTR-026 — Complete deployment and integration acceptance** — TODO
  - Owner: unassigned.
  - Depends on: HTR-021, HTR-022, HTR-023, HTR-025.
  - Done when: Pass real row-to-cell-to-line transcription, review/correction, offline operation, restart/retry, resource limits and external-app round trip. Record agreed quality/latency results and known failure classes; publish operator and caller documentation with exact tested commands.

- [ ] **HTR-027 — Evaluate targeted model fine-tuning** — TODO
  - Owner: unassigned.
  - Depends on: HTR-022, HTR-026.
  - Done when: Optional follow-up: determine whether baseline errors justify training, verify corrected-data volume and licenses, split by page/hand, and estimate GPU/storage needs. Proceed with training only under an explicit implementation decision; compare held-out results and preserve model lineage. No fixed page-count accuracy promise.

- [x] **HTR-028 — Name project and publish initial planning repository** — DONE
  - Owner: current chat.
  - Depends on: HTR-002.
  - Done when: ScriptaLocal has a README, safe ignore rules, and planning documents published to penica/ScriptaLocal with the local checkout connected.
  - Completion, 12 November 2026: Added project identity, README and ignore rules; published planning documents and connected the local checkout. Application implementation remains open.

- [ ] **HTR-029 — Make operational settings and backup destination configurable** — TODO
  - Owner: unassigned.
  - Depends on: HTR-007, HTR-019.
  - Done when: Configure storage/backup roots, service access, GPU allocation, models, retention and resource limits without source edits. Support local backup now and a verified Synology NFS mount later; reject an unavailable required mount rather than writing into its underlying local directory. If a management UI is implemented, expose validated backup destination/settings there with protected secrets. Verify changes, restart persistence and inaccessible-destination errors.
  - Evidence, 12 November 2026: User requested configurable backup and minimal hardcoding. Initial environment example includes temporary local backup root; UI and NAS validation are not implemented.
