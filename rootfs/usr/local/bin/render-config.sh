#!/usr/bin/env bash
# render-config.sh — s6 oneshot stub for phase C-boot.
# The full implementation (env resolution, secret loading, template rendering)
# is provided in phase D. This stub creates the minimum files required for the
# mail daemons to start so the C.9 integration test can validate the s6 graph.
set -euo pipefail

log() { printf '[render-config] %s\n' "$*"; }

log "stub: creating minimum bootstrap files for C-boot smoke test"

# postsrsd needs a secret file to start.
if [ ! -f /etc/postsrsd.secret ]; then
    openssl rand -hex 32 > /etc/postsrsd.secret
    chmod 600 /etc/postsrsd.secret
    log "generated /etc/postsrsd.secret"
fi

log "stub complete — real configuration rendering provided by phase D"
exit 0
