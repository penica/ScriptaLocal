# Validation evidence

Validation started 12 November 2026. Application acceptance is not established by these foundation probes.

## Storage

`host-inventory.json` records exact inspection commands and output; `storage.json` records local write/fsync/atomic-rename/read checks and a checksummed temporary backup-file round trip. The root filesystem had 179 GiB free before image downloads. Build images remain in Docker's existing `/var/lib/docker`. Models, artifacts, PostgreSQL, build cache and reference images use configurable ignored project `data/` subdirectories. No existing data was deleted and no filesystem/driver/daemon settings were changed.

Local backup destination is `/srv/storage/scriptalocal-backups`, selected by the user pending a Synology NFS mount. It is owned by the current user and restricted to mode 0700. The existing pool is ONLINE with no known data errors, but one member has five recorded read errors. A file round trip is not a database restore or off-machine disaster-recovery test. HTR-025 remains open.

## GPU runtime

Base image: `pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime@sha256:2b59b1b91885677814f78be1f8df48a25d5dc952eb6580eaecfefca510f9afd3`.

Ran separately for devices 0 and 1 from the project root:

```bash
docker run --rm --network none --device nvidia.com/gpu=0 \
  -e VALIDATION_OFFLINE=1 -e OMP_NUM_THREADS=2 \
  -v "$PWD/scripts/validation:/validation:ro" \
  pytorch/pytorch@sha256:2b59b1b91885677814f78be1f8df48a25d5dc952eb6580eaecfefca510f9afd3 \
  python /validation/runtime_probe.py
```

Repeat with device `1`. `gpu-0-runtime.json` and `gpu-1-runtime.json` show distinct GPU UUIDs, exactly one CUDA device each, matching CPU/GPU matrix products and a real cuDNN convolution. Both probes passed with networking disabled. Existing CDI configuration worked; no runtime registration or restart was required. Other Docker services stayed running.

## Dependency validation

The candidate Dockerfile uses the pinned base above and bounded initial direct requirements. Kraken 6.0.3 supports the existing PyTorch 2.7.1 candidate; the currently published Kraken 7.1.1 requires PyTorch >=2.9. Separate environments remain an option if actual model compatibility requires them. This is a measured baseline selection, not a claim that newer versions cannot run on the machine.

```bash
make validation-build
# Set an immutable tested image ID, not a floating tag.
export SCRIPTALOCAL_VALIDATION_IMAGE=$(docker image inspect scriptalocal-validation:initial --format '{{.Id}}')
make validation-config
make validation-gpu0
make validation-gpu1
```

The dependency probe exercises CPU OpenCV morphology, NumPy/SciPy/scikit-image, Kraken binarization and segmentation imports, and GPU generation using a tiny randomly initialized TrOCR architecture. It proves library/kernel compatibility only. Real model loading, Kraken learned segmentation, handwriting recognition accuracy, FP16 acceptance and long-line memory behavior require separate evidence.

After a successful build and probe, capture the complete resolved package lock and immutable image identity. The initial requirements file alone is not a fully reproducible transitive lock.

## Outstanding validation

- Real manuscript samples: put images in `data/reference/row-crops/` and optional transcripts in `data/reference/ground-truth/`. One reference crop has now been supplied; ground truth and the larger corpus remain pending.
- Existing application integration: give its coder the request in `../existing-app-handover.md`.
- Learned segmentation and real TrOCR model offline inference need pinned model files and samples.
- Quality comparison, one/two-GPU pipeline benchmarking, service boundaries, leased-job recovery, actual database restore and final integration depend on the corresponding implementation tasks. Runtime smoke tests do not complete those tasks.
- Synology NFS mount and off-machine recovery remain future operational validation; backup configuration must not be hardcoded in service logic.

## Completed dependency and real-weight checks

The locked rebuild passed `pip check`; all 165 installed package versions exactly match `deploy/validation/constraints.lock`. `dependencies-locked-gpu-0.json` records successful CPU-library and GPU-generation probes. The Dockerfile now constrains transitive versions in addition to the digest-pinned base. Raw pip freeze data includes base-image build paths; use the normalized constraints file for rebuilds.

`pretrained-gpu-1.json` records offline inference with 333,921,792 real Bern model parameters at FP32, using about 1.40 GB peak allocated CUDA memory. Model loading took 1.584 s and bounded generation 1.040 s in that synthetic-input smoke test. These numbers are not an end-to-end service benchmark. `trocr-model-manifest.json` pins the upstream revision and file checksums; only safetensors inference weights were downloaded, not pickled training checkpoints.

The bundled Kraken learned model ran offline on CPU, taking 167.483 s on a synthetic three-line cell. It produced 22 detections, so this establishes executable runtime support but clearly does not establish acceptable line segmentation. Default model height is 1800 pixels and needs investigation for short cell crops. Real-sample GPU validation is underway; this candidate is not selected for production based on the synthetic result.

The user supplied one 2418 × 272 manuscript row crop. It remains private in ignored storage. A manually bounded cell is used for the first functional probe; automatic cell detection, multi-sample accuracy and caller integration remain open.

The first real-cell probe exposed a Kraken 6.0.3 default-model loader error under Python 3.11 (`resources.files` applied to `kraken.blla`, which is a module rather than a package). The probe now resolves and loads the bundled candidate explicitly using `TorchVGSLModel.load_model`; the original failure log is retained privately. This workaround must be used by any adapter selecting those weights.

The unpadded real-cell segmentation found zero lines in a visibly three-line cell. That result is a segmentation failure, not an empty transcription. Its summary is explicitly marked needs-review in `manuscript-cell-summary.json`. A subsequent probe pads the cell to the model canvas height to preserve its pixel scale; its outcome is tracked separately. No automatic divider detector or accuracy result is implied.

The scale-preserving real-cell probe found and visually isolated three lines, then executed TrOCR recognition on each offline. It took 7.455 s including segmentation and recognition initialization/generation, with 1.40 GB peak allocated CUDA memory. See `manuscript-cell-padded-summary.json`; private crop images and raw text remain in ignored `data/validation/manuscript-cell-padded/`. No CER/WER can be computed without a corrected transcript.

The exploratory OpenCV divider overlay initially produced 20 intervals including thin outside-table margins. Explicit outer-rule bounds removed those margins, leaving 18 candidate interior cells. The center fold/blank regions and column semantics still require review. This is not yet the robust detector's difficult-case acceptance.

## Image validation and public contract

`cpu-contract-images.json` and `cpu-tests.txt` record 24 passing tests in a separately pinned, network-disabled, read-only CPU container with no GPU requested. The complete 27-package constraint snapshot is `deploy/cpu-validation/constraints.lock`; the final rebuild matched that snapshot exactly. `make validation-cpu-build` and `make validation-cpu` reproduce these checks.

`scriptalocal/images.py` preserves original bytes and uses content-addressed, fsynced atomic publication. It validates image formats, MIME, byte/pixel/side/frame limits and EXIF transforms. TIFF dimensions are taken from encoded-raster tags because Pillow may already report oriented dimensions. Verification uses a fresh parser because inspecting PNG EXIF can change parser state. Tests compare actual decoded pixel positions for all eight orientations of PNG and TIFF and verify reversible crop/rotation/scale/padding mappings. `real-image-input.json` records byte-preserving decode/publication of the user's real JPEG.

The designed HTTP contract is in `../api/openapi.json`, with fictional examples and behavioral documentation in `../api/README.md`. Pydantic and OpenAPI structural validators enforce schema invariants. This does not claim an implemented HTTP service, database, queue, authentication, backup restore or existing-app integration.

## Layout and full manual-cell provenance

`layout-validation.json` and `layout-tests.txt` are the latest 46-test evidence, using a rebuilt 29-package CPU environment whose installed versions match its lock. Earlier 24-test reports/hashes remain historical snapshots. Geometry implementation/limitations and reproduction are documented in `../layout-validation.md`. The real row yields 18 reviewable cells; the larger annotated corpus is still missing.

`model-profile.json` selects the pinned initial recognition and segmentation baseline and records license sources and actual model sizes. `manuscript-provenance-summary.json` records a fresh three-line real-cell run with model/configuration/source hashes, timings and VRAM. The run used UID 1000, no networking, a read-only container root and read-only models, with writable validation outputs only. `manuscript-api-summary.json` proves its text-preserving v1 schema export; its job ID is a CLI artifact and not an existing HTTP job. No raw manuscript text or images were published.

## Agent upload service

`intake-validation.json` and `intake-tests.txt` record 18 integration checks in the separately pinned 20-package intake image, running as UID 1000 with read-only root storage, no network during tests and dropped capabilities. `intake-http.json` records actual host-to-container upload, unchanged-byte checksum, restart/replay, revocation, log credential checks and the current loopback/nonroot/read-only deployment. The core suite remains separate and still passes all 46 checks. See `../agent-uploads.md` for commands. Other-agent reachability and its actual contract-file upload remain pending; no remote exposure or OCR job service is claimed.

## Durable queue and scoped file transfer updates

`jobs-queue.json`, `jobs-tests.txt` and `jobs-restart.json` record 20 real PostgreSQL tests and actual forced-stop recovery. See [queue reproduction](../job-queue.md). This completes HTR-016 at queue-library scope; OCR routes/resident workers remain open.

The actual coding-agent two-file upload is now verified in `integration-handover-received.json`. The selected LAN interface is documented in `intake-lan.json`. Scoped downloads add explicitly published snapshots, separate read-only tokens and manifests; `intake-download-http.json` and `intake-download-tests.txt` record LAN checksum verification and all 22 intake checks. Older loopback/pending-upload statements above describe historical snapshots.

## Registers synthetic fixture exchange

The 43,672-byte fixture ZIP checksum, intake registry receipt and all 42 manifest entries match. Its 40 pytest checks pass unchanged in a locked 15-package network-disabled runner. `app-fixtures.json` records stricter service-model validation: 18/21 fixtures pass, with three missing warning scopes. `app-fixtures-proposed-fix.json` records 21/21 after explicit proposed in-memory warning additions, with original bytes untouched. `semantic-bundle.json` proves the standalone outgoing reference package reproduces these outcomes without the project checkout mounted. `contract-cli-tests.txt` records the complete 56-test core suite, including three CLI checks for validity, byte limits and input-body redaction. `contract-clarification-download.json` verifies both outgoing files through the LAN intake. [Contract clarification](../registers-contract-clarification.md) records answers and open service release gates. This is offline fixture compatibility, not deployed API/application acceptance.

## Revised fixtures and resident recognition

`app-fixtures-revised.json` and `app-fixture-revised-tests.txt` establish 21/21 canonical service fixtures and 52 unchanged upstream tests for the corrected bundle; the three own-scope warning arrays are the only canonical changes.

`recognition-comparison.json`, `recognition-{fp32,fp16,oom,truncation}.json` and `recognition-tests.txt` record resident-model reuse, identical text hashes across precisions on three real line crops, controlled CUDA OOM recovery, real token-limit truncation and the complete 63-test core suite. [Recognition reproduction and limits](../recognition-validation.md) distinguishes these adapter checks from pending leased-worker, HTTP and manuscript-quality acceptance.

## Leased stage orchestration

`worker-orchestration.json`, `worker-tests.txt` and `worker-core-tests.txt` record 16 real-PostgreSQL worker checks, the original 20 queue checks after query/connection changes, and 65 core checks. [Worker behavior/reproduction](../worker-validation.md) covers independent heartbeats, cancellation/lease fencing, committed predecessor scope, profile routing, configurable database bounds and fail-closed outages. These are generic-handler checks; actual full-row GPU stage assembly and deployment remain open.
