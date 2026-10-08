# cloudflared plus a small watchdog (watchdog.sh) that restarts the quick tunnel when Cloudflare has dropped it.
# The official image has no shell, so its static binary is copied onto Alpine.
FROM cloudflare/cloudflared:2026.10.0 AS cloudflared

FROM alpine:3.22
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY watchdog.sh /usr/local/bin/tunnel-watchdog.sh
USER 65532:65532
ENTRYPOINT ["/bin/sh", "/usr/local/bin/tunnel-watchdog.sh"]
