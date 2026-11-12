# ScriptaLocal

Local handwriting transcription for historical table-row images.

Pipeline: **OpenCV cell detection → Kraken line segmentation → TrOCR Kurrent recognition**.

Provides a planned HTTP backend for an existing crop-selection application. Preserves cell order, line breaks, empty cells and original-image geometry. Deployment uses Docker Compose with NVIDIA GPU workers.

## Project status

Planning is complete. The application, containers and models are not installed or implemented yet.

- [Implementation plan](docs/implementation-plan.md)
- [Task checklist](tasks.md)
- [Agent instructions](AGENTS.md)

Initial implementation priorities are safe storage capacity, container GPU validation and dependency compatibility on the host CPU.

No project license has been selected yet. Model and dependency licenses are tracked separately.
