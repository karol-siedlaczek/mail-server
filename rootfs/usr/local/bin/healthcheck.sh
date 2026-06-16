#!/bin/bash
# Aggregate liveness probe for Docker HEALTHCHECK. Healthy only when every
# in-image daemon answers and, if Redis is configured, Redis PINGs. Per-daemon
# restart is handled by s6; this gates the *container's* health status.
#
#   postfix status          → master is running
#   doveadm service status  → Dovecot services up (any output = alive)
#   rspamadm control stat   → Rspamd controller answers
#   redis-cli PING          → only if REDIS_HOST is set
#
# Reads the rendered env from /run/s6/container_environment (s6 contenv) so the
# values resolved by render-config (incl. __FILE secrets) are visible here.
set -eu

# Import s6 container environment, if present (HEALTHCHECK runs outside s6).
if [ -d /run/s6/container_environment ]; then
    for f in /run/s6/container_environment/*; do
        [ -e "$f" ] || continue
        name="$(basename "$f")"
        val="$(cat "$f")"
        export "${name}=${val}"
    done
fi

fail() { echo "UNHEALTHY: $1" >&2; exit 1; }

postfix status                 >/dev/null 2>&1 || fail "postfix not running"
doveadm service status         >/dev/null 2>&1 || fail "dovecot not running"
rspamadm control stat          >/dev/null 2>&1 || fail "rspamd controller not answering"

if [ -n "${REDIS_HOST:-}" ]; then
    redis_args=(-h "${REDIS_HOST}" -p "${REDIS_PORT:-6379}")
    [ -n "${REDIS_PASSWORD:-}" ] && redis_args+=(-a "${REDIS_PASSWORD}" --no-auth-warning)
    [ -n "${REDIS_DB:-}" ] && redis_args+=(-n "${REDIS_DB}")
    pong="$(redis-cli "${redis_args[@]}" PING 2>/dev/null || true)"
    [ "$pong" = "PONG" ] || fail "redis not answering at ${REDIS_HOST}:${REDIS_PORT:-6379}"
fi

echo "healthy"
