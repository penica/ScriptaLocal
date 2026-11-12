# Persistent local deployment

The Compose project `scriptalocal` is running on this host. API: `http://127.0.0.1:8090`. PostgreSQL and the single GPU worker are private. This is the transcription service; the separate agent handover service remains at `http://10.10.100.209:8091`. Registers integration and opt-in LAN HTTPS remain separate acceptance work.

The API and worker run as the configured nonroot host UID/GID, with read-only root filesystems, all capabilities dropped, no-new-privileges, bounded tmpfs, PID limits and CPU/RAM caps. Their code is baked into immutable images; there is no source bind mount. The database uses a read-only root filesystem, a persistent data mount and the initialization capabilities required to become its PostgreSQL OS user. All three services use `unless-stopped` and Docker log rotation (5 MiB × 2 files per container). API and worker health checks require actual pipeline readiness, not just an open port. Host reboot behavior is configured by the restart policy; a host reboot was not tested.

## Configure a new installation

These commands create a **new** instance; this host already has `data/service/`. The setup tool refuses existing roots and never rotates credentials implicitly. Run it as an unprivileged user. Plain absolute paths without whitespace or shell metacharacters are currently required for Compose environment entries.

```sh
docker build -f deploy/service/api.Dockerfile -t scriptalocal-api:provisioned .
docker build -f deploy/service/worker.Dockerfile -t scriptalocal-worker:provisioned .
python3 -m scripts.configure_service \
  --root data/service \
  --model-root data/models/trocr-kurrent-XVI-XVII \
  --model-manifest-sha256 d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445 \
  --api-image "$(docker image inspect scriptalocal-api:provisioned --format '{{.Id}}')" \
  --worker-image "$(docker image inspect scriptalocal-worker:provisioned --format '{{.Id}}')" \
  --backup-destination /srv/storage/scriptalocal-backups
```

The build tags are operator conveniences. Generated `compose.env` and `worker.json` pin the actual immutable image IDs. Base image digests and exact package locks are in the Dockerfiles. API: 24 installed distributions; worker: 171, checked against the existing full locks. Kraken/TrOCR remain in the GPU environment; FastAPI runs in the separate CPU environment. GPU assignment defaults to `nvidia.com/gpu=0`; `--gpu` selects another single CDI device. Models are mounted read-only and checksum-verified before readiness, with both inference offline flags enforced. The worker network is internal-only.

Generated `settings/api.json` and `settings/worker.json` contain configurable paths, limits and pipeline references. `settings/credentials.json` contains token hashes/capabilities/owners, not bearer tokens. Raw application and health tokens are in the private `secrets/` directory. The database DSN is also a protected file, never an environment password. Roots/directories are mode 0700; application secrets are 0600. Only database initialization password files use 0444 so the separate PostgreSQL OS user can read their explicit container mounts; their host parent directories remain 0700. Do not broaden those directories or mount the whole secrets directory into another service.

`settings/operator.json` records the selected backup destination for subsequent operator tooling; it does not create an automatic backup schedule. There is no new web interface. User-selected backup/storage settings remain file-configurable pending any future administration UI.

## Start and maintain

```sh
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml --profile tools config --quiet
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml up -d --wait db
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml --profile tools run --rm migrate
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml up -d --wait --wait-timeout 120 api worker
```

Migrations are an explicit operator step; health requests and worker startup never migrate. The application connects as `scriptalocal_app`, a database owner without superuser, role-creation, database-creation, replication or bypass-RLS privileges. It cannot call server-file reading functions. Initial role/database creation is performed by PostgreSQL initialization SQL for a new cluster only; changing password files later does not itself rotate an existing database role.

Caller credentials can be added/revoked with an atomic replacement of `settings/credentials.json`. The API mounts the settings directory read-only and reloads credentials per request, so atomic replacement is visible immediately. Keep existing owner identities stable if access to their jobs should persist. The live add/revoke check passed without an API restart. Configuration/image changes require coordinated process recreation; update both the selected worker image and its `runtime_image` setting together. Historical jobs/revisions retain pinned configuration identities; keep their matching worker/configuration available rather than silently processing them under changed models/code.

For the validated maintenance restart, wait for active work to finish, stop API/worker, restart the database, then run the final `up --wait` command above. Commands used:

```sh
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml stop --timeout 150 worker api
docker compose --env-file data/service/compose.env -p scriptalocal \
  -f deploy/service/compose.yaml restart --timeout 20 db
```

Do not remove persistent directories or use destructive volume cleanup as a restart procedure. Release rollback and backup automation remain HTR-025 work. The earlier [local restore validation](backup-restore-validation.md) is not a scheduled backup facility.

## Validation and storage

All three setup-tool tests and all 65 core checks pass. A separate fresh PostgreSQL cluster validated SCRAM application login with the final initialization SQL; that temporary cluster is stopped. Application-role checks confirmed all five elevated-role flags false and denied server-secret reads. A parameterized constraint-error probe did not expose its synthetic value in server logs. Logging settings follow [PostgreSQL 17 logging controls](https://www.postgresql.org/docs/17/runtime-config-logging.html); function access is described in [server administration functions](https://www.postgresql.org/docs/17/functions-admin.html). Logs were also checked for all generated credentials.

One real HTTP job through the baked production images returned 18 cells/24 lines in 67.973 seconds. Idempotent replay retained its job ID. After the complete maintenance restart, health was ready and result bytes/original SHA-256 were unchanged. Reported worker image identity and source fingerprints match the running deployment. See `docs/validation/service-deployment.json`.

Measured after that single job: PostgreSQL allocated 47,452 KiB; artifacts 784 KiB; originals 300 KiB; settings/status/secrets together 56 KiB. Logical image sizes: API 260,817,431 bytes; worker 11,056,096,830 bytes. These image sizes share existing layers and must not be summed as new disk consumption. The approximately 1.34 GB model remains at its existing read-only mount. Retention, overall job deadlines, LAN HTTPS and external application acceptance remain open; model/training improvements are outside the current critical path.
