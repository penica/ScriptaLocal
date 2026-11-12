# ScriptaLocal

Local handwriting transcription for historical table-row images.

Pipeline: **OpenCV cell detection → Kraken line segmentation → TrOCR Kurrent recognition**.

Provides a planned HTTP backend for an existing crop-selection application. Preserves cell order, line breaks, empty cells and original-image geometry. Deployment uses Docker Compose with NVIDIA GPU workers.

## Project status

Foundation validation is in progress. Both GPUs passed isolated CUDA container tests, and the pinned Bern model passed offline real-weight inference on a synthetic line. The application service and manuscript-quality acceptance are not implemented yet.

- [Implementation plan](docs/implementation-plan.md)
- [Task checklist](tasks.md)
- [Agent instructions](AGENTS.md)

Initial implementation priorities are safe storage capacity, container GPU validation and dependency compatibility on the host CPU.

No project license has been selected yet. Model and dependency licenses are tracked separately.

## Validation and integration

- [Validation evidence and commands](docs/validation/README.md)
- [Handover request for the existing app coder](docs/existing-app-handover.md)
- Local manuscript input folder: `data/reference/row-crops/` (ignored by Git).
- Configuration template: `.env.example`; backup storage is configurable for a future Synology NFS mount.
