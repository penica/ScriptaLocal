# Trusted LAN endpoint and Registers handover

The user selected `scripta.penic.net` and authorized Let's Encrypt with Cloudflare DNS-01. The service is running at `https://scripta.penic.net:8093`, bound specifically to `10.10.100.209`. Cloudflare has a DNS-only A record to that private address. Normal system CA/hostname verification passes with the issued Let's Encrypt certificate; no private CA installation or public inbound port 80/443 is needed. Loopback HTTP remains `127.0.0.1:8090`; the user-selected intake HTTP binding remains unchanged on port 8091.

The pinned gateway image builds Caddy v2.11.9 with `caddy-dns/cloudflare` v0.2.4, from the two digest-pinned images in `deploy/service/gateway.Dockerfile`. The runtime binary's privileged-port file capability is removed because the container drops all capabilities and listens on unprivileged port 8443. The gateway runs as UID/GID 1000 with a read-only root, bounded resource/log/tmpfs settings and only the frontend network. Its health check checks TCP liveness; the separate HTTPS probe establishes actual certificate trust and API readiness.

The existing `caddy-z420` service was inspected read-only and was not reconfigured or restarted. Its existing CF credential was reused privately at `data/service/secrets/cloudflare-api-token` (0600, parent 0700), mounted only into this gateway at `/run/secrets/cloudflare_api_token`. It is referenced through Caddy's file placeholder, not embedded in configuration, environment arguments, Git or agent credentials. API/worker do not receive it. ACME state and managed certificate keys persist under `data/service/caddy-data`; the admin endpoint/config autosave/access logging are disabled. Renewal is configured by Caddy; a future renewal has not been observed.

This network has split DNS: ordinary DNS queries for penic.net see internal nameservers and could not see the challenge TXT, while an HTTPS public DNS query confirmed the correct Cloudflare nameservers and TXT. The issuer therefore waits 20 seconds and skips its local propagation precheck (`propagation_timeout -1`); Let's Encrypt still performs the actual public DNS ownership validation. Certificate chain/hostname verification remains enabled. HTTP and TLS-ALPN challenges are explicitly disabled.

## Configure and run

The default Compose file remains loopback-only. LAN is an explicit overlay/profile. For a new deployment, build the gateway image, then run `scripts.configure_lan` with the hostname, private bind address, immutable gateway image ID and a protected Cloudflare token file. It refuses existing LAN configuration. The CLI also requires the already configured base service.

```sh
docker build -f deploy/service/gateway.Dockerfile -t scriptalocal-gateway:provisioned .
python3 -m scripts.configure_lan --root data/service \
  --hostname scripta.penic.net --address 10.10.100.209 \
  --proxy-image "$(docker image inspect scriptalocal-gateway:provisioned --format '{{.Id}}')" \
  --cloudflare-token-file /protected/path/cloudflare-token
docker compose --env-file data/service/compose.env --env-file data/service/lan.env \
  -p scriptalocal -f deploy/service/compose.yaml -f deploy/service/compose.lan.yaml \
  --profile lan up -d --wait gateway
```

These setup commands describe a new installation; the current instance is already configured. Certificate and DNS changes must preserve unrelated records and the existing proxy. Token changes require replacing the protected file and recreating the gateway so its file bind/provisioned provider refreshes.

## Evidence and integration delivery

65 core and two LAN-setup tests pass. A complete real HTTPS upload/idempotent replay/poll/result run returned 18 cells and 24 lines in 67.562 seconds. Default system trust, authenticated readiness, 401 without credentials, wrong TLS name rejection, unknown Host 421, no untrusted CORS origin and HTTPS-preserving slash redirects pass. Inference remains on the private GPU worker; outbound ACME/DNS operations belong to the certificate gateway, not an OCR cloud fallback. Generated credentials were absent from gateway/API/worker logs. See `docs/validation/lan-access.json`.

Only GET of a ticket manifest or selected file under `/intake/v1/downloads/{id}[/files/{id}]` is proxied to the existing intake for encrypted handover. Intake administration/uploads are not routed by this rule. Four checksum-verified snapshots were shared for Registers: `connection.json`, `builder-implementation-handover.md`, `openapi.json`, and `semantic-validation.md`. The connection file has an isolated 24-hour registers-acceptance credential, not the CF or administrator credential. Registers should combine these with its application contract/revised fixtures and prepare Builder's app-specific instructions. The private copy-and-paste prompt is `data/validation/registers-copy-prompt.txt`; never commit it.

Local HTTPS tests are complete. Reachability, provider implementation and a real round trip from the Registers worker remain HTR-021 acceptance work. The handover does not authorize bypassing that application's deployment rules or writing production research records for connectivity tests.
