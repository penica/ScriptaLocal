FROM caddy@sha256:6350162b3889ca32600b3c699c61572fa0eb3ad6b2a51874f19b158a7df77511 AS builder
RUN xcaddy build v2.11.9 --with github.com/caddy-dns/cloudflare@v0.2.4 --output /usr/bin/caddy
FROM caddy@sha256:78bd35025c7ba85961dd149ae670315129954e77abe2383ac47c89106b35ade9
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
RUN setcap -r /usr/bin/caddy
USER 1000:1000
