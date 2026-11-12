# Handover for the existing application's AI coder

Copy the following request to the coder responsible for the crop-selection application.

---

We are building ScriptaLocal, a local handwriting transcription backend, in a separate repository: https://github.com/penica/ScriptaLocal . Please inspect the existing application's actual code and provide an integration handover. Do not replace its interface or its current Codex provider. At this stage, document the current behavior and propose the smallest provider adapter; do not claim an endpoint is implemented in ScriptaLocal.

The input is a cropped historical table row. Vertical cell dividers exist only as lines in the image, and each cell may contain multiple handwritten lines. The local service will detect cells with OpenCV, extract text lines with Kraken and recognize them using TrOCR Kurrent. It must preserve cell order, blank cells and line breaks.

Please return a Markdown integration document with these details, using exact source file paths and function/route names:

1. **Current architecture and deployment.** Frontend/backend languages and frameworks, where crop generation runs, where the Codex process runs, where the app is hosted relative to the GPU machine, and whether its backend can call an HTTP service on that machine. Identify any container networking constraints. Do not assume localhost in the browser points at the GPU server.
2. **Crop input contract.** Where the selected crop is created, its format and MIME type, typical/max dimensions and byte sizes, orientation/rotation handling, whether it is a file/blob/base64/path, and whether the app retains original scan dimensions, selection coordinates, scale and rotation. Include a sanitized request example. Confirm whether the whole table-row crop is submitted as a single image and whether surrounding context is included.
3. **Current transcription provider contract.** Exact caller signature, request and response types, current Codex CLI invocation and output parsing, synchronous versus asynchronous behavior, timeout/cancellation/retry handling, provider selection and configuration locations. Include one sanitized successful result and one error example. Never include credentials or private manuscript text; placeholders are sufficient.
4. **Result import contract.** How transcription text is inserted or saved, how cells/columns are represented, which stable row/cell IDs must survive, whether ordered cells with newline-preserving text can be imported, and how blank/unreadable/failed states are represented. Explain save/revision conflict handling and whether results can overwrite manual corrections. If the app currently accepts only plain text, propose the smallest explicit mapping from service cells to existing fields and preserve the structured JSON.
5. **Networking and credentials.** Suggest a server-side adapter that uploads image bytes to a configurable service base URL with a protected bearer credential, polls jobs and retrieves results. State where configuration belongs. Describe any reverse proxy, HTTPS, authentication or CORS requirements without sending real secrets. Browser-direct access should be justified if server-side access is impossible.
6. **Provider adapter proposal.** List exact files likely to change and any migration required. Keep Codex as an explicit selectable provider; never silently fall back to a cloud provider. Show how Local HTR exposes progress, review warnings, retry and cancellation without duplicate imports. Define expected maximum response waiting time and whether polling survives navigation/restart.
7. **Integration fixtures and acceptance.** Supply sanitized request/result fixtures and the commands needed to run the relevant app locally or in a test environment. Include a test for a row with at least three cells, a blank middle cell and two text lines in another cell, plus a failed job, cancellation and a stale result that must not overwrite human edits. Specify any questions the ScriptaLocal developer must resolve before coding the adapter.

The planned ScriptaLocal contract is versioned and asynchronous:

- `POST /v1/jobs`: multipart image upload with optional client reference, model and corrected divider geometry; returns HTTP 202 and a job ID; supports an idempotency key.
- `GET /v1/jobs/{id}`: queued/running/succeeded/failed/cancelled state and stage/warnings.
- `GET /v1/jobs/{id}/result`: ordered cells with ordered lines, newline-preserving text, explicit states, original-crop coordinates, model provenance and timings.
- `POST /v1/jobs/{id}/cancel`: best-effort cancellation.
- Corrections and artifacts have separate versioned endpoints.

These routes are a design, not a running service. Propose required field names in the handover so both developers can agree on a concrete OpenAPI schema before implementation. Keep cell index separate from your app's domain IDs, and make coordinate units/origin/rotation explicit.

Please deliver facts from the code, mark unknowns explicitly, and separate current behavior from proposed changes. Save the handover in your repository and provide its path, the relevant source links, and the sanitized fixtures to the ScriptaLocal developer.

---

## Local sample drop location

Put representative row-crop images in `/home/penica/eScriptorium/data/reference/row-crops/`.

Optional human transcripts belong in `/home/penica/eScriptorium/data/reference/ground-truth/`. Use the same image stem, for example `page-001-row-02.png` with `page-001-row-02.json`. Include ordered cells and each cell's text lines, preserving original spellings and empty cells. Record uncertainty rather than guessing. Full annotation format will be finalized with the evaluation corpus.

Start with a few varied crops to unblock functional inference; quality acceptance needs the larger representative corpus described in the implementation plan. Useful variations include clear/faint/broken borders, blank cells, slant, handwriting crossing borders, different scribes, and German/Latin/name passages. Keep source page IDs so evaluation can split by page/hand. Image files and transcripts remain ignored by Git.

## Designed contract now available

ScriptaLocal now has a schema-tested v1 design in [docs/api/README.md](api/README.md), [OpenAPI JSON](api/openapi.json), and [fictional examples](api/examples/). Please use those concrete field names for the adapter proposal and identify any incompatibility with the existing app. The HTTP routes are not implemented yet.
