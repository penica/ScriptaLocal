# ScriptaLocal

Local handwriting transcription for historical table-row images.

Pipeline: **OpenCV cell detection → Kraken line segmentation → TrOCR Kurrent recognition**.

Provides a planned HTTP backend for an existing crop-selection application. Preserves cell order, line breaks, empty cells and original-image geometry. Deployment uses Docker Compose with NVIDIA GPU workers.

## Project status

Foundation validation is in progress. Both GPUs passed isolated CUDA container tests, and the pinned Bern model passed offline real-weight inference on a synthetic line. The OCR job service and manuscript-quality acceptance are not implemented yet.

- [Implementation plan](docs/implementation-plan.md)
- [Task checklist](tasks.md)
- [Agent instructions](AGENTS.md)

Initial implementation priorities are safe storage capacity, container GPU validation and dependency compatibility on the host CPU.

No project license has been selected yet. Model and dependency licenses are tracked separately.

## Validation and integration

- [Validation evidence and commands](docs/validation/README.md)
- [Handover request for the existing app coder](docs/existing-app-handover.md)
- [Designed API contract and examples](docs/api/README.md)
- [Layout and manual-cell validation](docs/layout-validation.md)
- Local manuscript input folder: `data/reference/row-crops/` (ignored by Git).
- Configuration template: `.env.example`; backup storage is configurable for a future Synology NFS mount.

## Agent file uploads

A separate [agent upload API](docs/agent-uploads.md) is running on the selected LAN address at `http://10.10.100.209:8091`. Operators issue expiring requests that name a destination/subfolder; agents upload directly using scoped credentials and receive SHA-256 receipts. The intake service has 18 passing integration tests plus actual HTTP/restart/retry validation. Upload through that address passed from a separate container; the coding agent’s two-file handover upload is complete and checksum-verified. Selected response documents can now be shared through separate expiring download tickets.

- [Response for the existing app coder](docs/registers-integration-response.md)
- [Durable queue implementation and validation](docs/job-queue.md)

- [Leased stage worker validation](docs/worker-validation.md)
