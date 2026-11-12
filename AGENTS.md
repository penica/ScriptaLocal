# Agent instructions

## Purpose and scope

Build a local handwriting transcription backend for an existing external web application. Input is an image of a table row: vertical dividers exist only in the pixels, and each cell may contain multiple handwritten lines. Preserve cells, line order, empty cells, original images, and uncertainty. This directory is a new project, not a checkout of upstream eScriptorium.

Read `docs/implementation-plan.md` and `tasks.md` before implementation. The selected deployment is Docker Compose on this host, using NVIDIA Container Toolkit. Do not introduce a VM, LXC layer, upstream eScriptorium deployment, or replacement annotation website without a documented reason tied to requirements.

## Task tracking

- `tasks.md` is the implementation checklist. Keep IDs stable and add tasks when scope is discovered.
- Use `- [ ] **HTR-NNN — Title** — TODO`, `DOING`, or `BLOCKED`; use `- [x] ... — DONE` only with evidence.
- Every task needs Owner and Done when. Record dependencies where applicable.
- On completion, record date, concrete changes, verification, and remaining limitations. Do not claim planned tests, inferred capabilities, or downloaded models as working inference.
- Keep unfinished work unchecked. Record the precise blocker and useful independent next work.
- Update the plan when an architectural decision changes; explain why.

## Implementation rules

- The independent agent intake uses a private SQLite transfer registry; this is not OCR job state. Keep its upload tokens scoped to an operator-selected destination, never expose administrator credentials, and keep the binding specific to the configured interface. The user explicitly selected direct LAN HTTP at `http://10.10.100.209:8091` for intake on 12 November 2026; do not revert that selection or broaden it to every interface.
- Run core tests from `tests/` in the CPU validation image and service integration tests from `integration_tests/` with the explicit component filename pattern and corresponding image; do not silently skip either required suite.
- Use FastAPI/Pydantic for the OCR API, PostgreSQL for durable job state, a PostgreSQL-backed leased job queue, CPU OpenCV preprocessing, Kraken line segmentation, and a separate Transformers/PyTorch TrOCR worker environment.
- Keep model adapters isolated to accommodate incompatible dependency requirements. Pin tested dependencies, container image digests, model revisions, checksums, and licenses. Do not use floating production tags or arbitrary remote model code.
- Benchmark one RTX 3060 first. Use explicit GPU assignments and one inference process per assigned GPU. Two 12 GB GPUs are not one 24 GB allocation. Distributed training is not the default.
- Preserve the original crop. Save transforms and cell/line coordinates relative to the original image. Never destructively erase border-crossing handwriting.
- Never send multiline cells directly to the Bern line recognizer. Do not invent a missing cell, illegible text, confidence percentage, or successful empty transcription.
- Distinguish blank, unreadable, failed, and needs-review states. Preserve raw output separately from human corrections. Do not translate or normalize names by default.
- Keep inference local after explicit provisioning. No automatic cloud fallback or silent dispatch to Codex. The existing app's Codex provider remains outside this service.
- Default OCR API binding is loopback. OCR LAN deployment requires scoped authentication and HTTPS through a configured proxy; the intake-specific user selection above is separate. Workers and database remain private. Do not mount the Docker socket into application containers.
- Keep scans, model caches, database files, credentials, and generated artifacts out of Git. Redact image content, tokens, and transcription bodies from routine logs.
- Check disk capacity before downloads/builds. Never delete unrelated Docker data, alter partitions/ZFS pools, or change host drivers merely to make room. Identify a safe storage location first.
- Verify host CPU instruction compatibility: this machine has AVX but no AVX2. A CUDA check alone does not validate CPU libraries.

## Cross-project collaboration

Agent role aliases: **Scripta** is this project’s local transcription backend/validation agent; **Registers** is the existing application’s integration contact; **Builder** is the agent assigned to implement and test changes in the Registers application. These name responsibilities, not permanent model/process identities. A replacement agent should state the role it is taking over and read the recorded handover. Other implementation agents belong to the application coding team and must be named by their actual assigned role; do not assume the integration contact owns every code change.

The existing application has a coding team; the current integration contact may differ from the agent that later implements changes. Keep contracts, decisions, acceptance criteria and evidence self-contained in versioned documents. Scope transfer tickets to a concrete deliverable, not an assumed persistent agent identity. A document handover does not authorize another project deployment or bypass its own instructions.

## Verification and handoff

Run focused tests for changed behavior and required integration checks. Geometry tests need difficult borders and overlapping handwriting; inference acceptance needs real manuscript samples with human ground truth. Use synthetic fixtures for geometry and error handling only, never as evidence of handwriting accuracy.

Record exact commands, versions, sample counts, hardware, errors, and measured results in implementation evidence. Do not invent accuracy or latency targets as achieved. Documentation-only changes need link/checklist consistency checks, not inference tests.

Treat host configuration and service restarts as operational changes: inspect existing usage, retain rollback material, and avoid interrupting unrelated workloads. Follow the user's existing authorization; do not add approval gates for routine reversible work.
