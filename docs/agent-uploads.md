# Agent upload API

ScriptaLocal now runs a separate file-intake API at `http://127.0.0.1:8091`. It accepts agent uploads into a folder selected when the operator creates an upload request. This is independent of the planned OCR job API and does not run uploaded files or start transcription.

The current binding is loopback only. An agent on another machine needs a reachable HTTPS endpoint or a configured private tunnel; that network location has not been supplied yet. Do not give a remote agent this machine's localhost URL as if it were reachable from there.

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
  python -m unittest discover -s /tests -v
```

Live HTTP validation additionally uploads through the headless client, checks saved bytes, restarts the Compose service, verifies an identical retry, revokes the test request, and checks that tokens are absent from logs. Remote-agent reachability remains unverified until the agent's network location is known.
