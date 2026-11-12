# Agent file transfer API

ScriptaLocal now runs a separate file-intake API at `http://10.10.100.209:8091`. It accepts agent uploads into a folder selected when the operator creates an upload request. This is independent of the planned OCR job API and does not run uploaded files or start transcription.

The user selected the host's `10.10.100.209/24` LAN address. The service binds specifically to `10.10.100.209:8091`, using direct HTTP as requested. A separate-container upload through this LAN address passed; the coding agent completed its two-file handover upload with verified checksums. The initial loopback-only setup remains historical validation evidence.

## Workflow

1. The operator creates a request naming a configured destination, optional subfolder, allowed suffixes, file/count/total-byte limits and expiry.
2. The service creates that folder and returns a random upload token restricted to the request. The administrator token is never shared with the uploading agent.
3. The agent sends one raw file per PUT request and receives its byte count, SHA-256 checksum and remaining file slots.
4. The operator reads the files locally. Repeating the same filename and content returns the same receipt; different content cannot overwrite an existing file.

Default destination aliases are `integration-handover`, `row-crops`, `ground-truth`, and `inbox`. Their actual directories are private configuration, not caller-provided filesystem paths. A new named subfolder can be created for each request. For example, `integration-handover/app-api-contract` resolves inside the configured handover root.

## Create a request on this host

The running Compose configuration is private at `data/intake/compose.env`. To request two API-contract files:

```bash
docker compose --env-file data/intake/compose.env \
  -p scriptalocal-intake -f deploy/intake/compose.yaml exec -T intake \
  python -m scriptalocal.intake_cli request \
  --destination integration-handover --subfolder app-api-contract \
  --label 'Existing application API contract' --max-files 2
```

This prints only the request ID, expiry and private file paths. It writes a mode-0600 JSON ticket and ready-to-send Markdown handover under `data/intake/requests/`. Give the uploading agent that scoped handover, not the administrator credential. Ticket files and uploaded content are ignored by Git.

The upload agent can use the JSON ticket with the repository's standard-library client:

```bash
python -m scriptalocal.intake_cli upload --ticket upload-ticket.json \
  /path/to/api-contract.md /path/to/integration-example.json
```

The upload command does not need the administrator configuration. It refuses HTTP redirects and prints receipts without printing credentials. An agent can also call the HTTP API directly without installing ScriptaLocal.

## HTTP protocol

| Method and path | Credential | Purpose |
| --- | --- | --- |
| `GET /health/live` | None | Liveness |
| `GET /intake/v1/destinations` | Administrator | List allowed destination aliases |
| `POST /intake/v1/requests` | Administrator | Issue a scoped upload request |
| `GET /intake/v1/requests/{id}` | Administrator or that request's token | Status, limits and file receipts |
| `PUT /intake/v1/requests/{id}/files` | That request's token | Upload one raw file |
| `POST /intake/v1/requests/{id}/revoke` | Administrator | Close the upload request |
| `GET /intake/v1/openapi.json` | Administrator | Routes, authentication and input schema |

Example administrator request body (the CLI fills defaults):

```json
{
  "destination": "integration-handover",
  "subfolder": "app-api-contract",
  "label": "Existing application API contract",
  "max_files": 2,
  "max_file_bytes": 10485760,
  "max_total_bytes": 20971520,
  "expires_in_seconds": 86400,
  "allowed_suffixes": [".md", ".json", ".yaml", ".yml", ".txt"]
}
```

Creation returns HTTP 201 with request details, `upload_url` and `upload_token`. Only a SHA-256 hash of the upload token is retained in the registry, so keep the returned ticket privately. Status responses do not return tokens or host filesystem paths.

For each upload, send `Authorization: Bearer <upload_token>`, `Content-Type: application/octet-stream`, and `X-Upload-Filename: <percent-encoded UTF-8 basename>`. Send the original file bytes as the body, not multipart, JSON or base64. A basic request looks like:

```bash
curl --fail-with-body --request PUT "$UPLOAD_URL" \
  --header "Authorization: Bearer $UPLOAD_TOKEN" \
  --header 'X-Upload-Filename: api-contract.md' \
  --header 'Content-Type: application/octet-stream' \
  --data-binary @api-contract.md
```

HTTP 201 means newly accepted; 200 means an identical retry. A receipt contains `request_id`, `file_id`, `filename`, `bytes`, `sha256`, `destination`, `subfolder`, `replayed` and `remaining_files`. A completed request permits identical retries but no extra filenames. Different content under an existing filename returns 409. Expired/revoked requests return 410, disallowed suffixes 415, byte limits 413 and temporary concurrency pressure 429. Error bodies contain a public code/message, retryable flag and request UUID; no file body, token or internal path is echoed.

## Configuration and operation

`data/intake/config.json` configures destination roots, the credential-file location, public base URL and optional server limits. `data/intake/admin.token` is a mode-0600 random administrator credential. `data/intake/compose.env` configures the immutable image, host data directory, UID/GID, bind address and port. Configuration is outside source control; do not hardcode a remote URL or secrets into the application. Changing the public base URL requires restarting only this intake service and issuing a handover with the correct reachable URL.

The Compose service has a read-only container root, runs as the configured unprivileged UID/GID, drops capabilities, and mounts only intake/handover/reference directories. It does not mount models, the Docker socket or the OCR database. Access logs are disabled and server logs must not contain credentials. File and folder traversal, symlink destinations and special-file collisions are rejected. Uploads are bounded while streaming, including requests without a Content-Length header. Tokens are rechecked before publication, so mid-upload revocation prevents acceptance.

Upload metadata uses a private SQLite WAL registry with process locking, atomic file publication, durable receipts and restart cleanup of interrupted reservations. This registry is specific to agent file transfer. OCR jobs still use the planned PostgreSQL queue; intake is not an alternative OCR job store. Uploaded documents remain inert until explicitly read by the operator/agent.

To revoke a request:

```bash
docker compose --env-file data/intake/compose.env \
  -p scriptalocal-intake -f deploy/intake/compose.yaml exec -T intake \
  python -m scriptalocal.intake_cli revoke REQUEST_UUID
```

For a fresh installation, build `deploy/intake/Dockerfile`, then run `python -m scriptalocal.intake_cli init --data-root /data` inside that image with the selected host data directory mounted at `/data` and `SCRIPTALOCAL_INTAKE_CONFIG=/data/intake/config.json`. Initialization validates configuration, writes private credentials, and refuses to overwrite an existing setup. Set the immutable image ID and host data root in a private Compose environment file, then run `docker compose ... up -d`. The existing host is already initialized.

## Validation

`integration_tests/test_intake.py` exercises token scopes, checksums/receipts, byte/count/total limits, safe retries, traversal/symlink/FIFO rejection, expiry/revocation (including during streaming), interrupted-upload restart recovery, chunked limits, concurrency, timeouts, unread unauthorized bodies, process locking and OpenAPI documentation. Run it in the pinned intake image, independently of the core image/layout suite:

```bash
docker run --rm --network none --read-only --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --cpus 2 --tmpfs /tmp:size=128m \
  -v "$PWD/integration_tests:/tests:ro" scriptalocal-intake:locked \
  python -m unittest discover -s /tests -p test_intake.py -v
```

Live HTTP validation additionally uploads through the headless client, checks saved bytes, restarts the Compose service, verifies an identical retry, revokes the test request, and checks that tokens are absent from logs. A separate network namespace has successfully uploaded through the selected LAN address. The coding agent completed its actual two-file upload; both hashes match its supplied receipts.

## Scoped downloads

Administrators can now publish fixed snapshots of selected files from a configured destination. This never exposes a folder listing or arbitrary filesystem path. Each share gets a separate expiring read-only token. Upload tokens cannot download, and download tokens cannot upload or administer the service. Only credential hashes are stored in the transfer registry; keep generated handovers private.

```bash
docker compose --env-file data/intake/compose.env \
  -p scriptalocal-intake -f deploy/intake/compose.yaml exec -T intake \
  python -m scriptalocal.intake_cli share \
  --destination integration-handover --subfolder scriptalocal-response \
  --label 'Integration response' --expires-in-seconds 86400 \
  registers-integration-response.md scriptalocal-openapi.json
```

This writes a private JSON ticket and Markdown download handover under `data/intake/requests/`. Give the receiving agent that handover: it supplies the manifest URL, per-file URLs, scoped bearer credential, expiry and SHA-256 checksums. The agent can use ordinary HTTP tools without installing this project. Use `Authorization: Bearer <download_token>`; never put credentials in query strings, follow redirects with credentials, or commit the handover.

| Method and path | Credential | Purpose |
| --- | --- | --- |
| `POST /intake/v1/downloads` | Administrator | Snapshot explicitly named files |
| `GET /intake/v1/downloads/{id}` | Matching download ticket or administrator | Read file manifest/checksums |
| `GET /intake/v1/downloads/{id}/files/{file_id}` | Matching download ticket | Download verified snapshot bytes |
| `POST /intake/v1/downloads/{id}/revoke` | Administrator | Revoke future reads |

Creation JSON contains `destination`, optional `subfolder`, `label`, `filenames` and optional `expires_in_seconds`. The configured file/count/total/expiry limits apply. Responses return `manifest_url`, `download_token` and manifest entries with `file_id`, `filename`, `bytes` and `sha256`. Downloads include attachment disposition, `X-Content-SHA256`, `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

Source files are copied into private immutable share snapshots before publication. Later source edits do not change a share. No-follow regular-file reads reject symlinks and FIFOs; every download verifies bytes against its receipt. Invalid credentials and out-of-scope files return 404. Matching expired/revoked tokens return 410. Revocation prevents new reads, but cannot recall bytes already sent. Shares persist across service restart. Expired snapshots currently remain in private storage until operator cleanup; expiry is access control, not automatic deletion.

Revoke with `python -m scriptalocal.intake_cli unshare <request_id>` inside the same Compose service. Twenty-two intake integration tests now cover uploads and downloads; a separate-container HTTP download through the LAN address verified both shared integration documents and rejected unauthenticated reads. See `docs/validation/intake-download-{http.json,tests.txt}`.
