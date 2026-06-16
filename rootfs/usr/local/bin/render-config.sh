#!/usr/bin/env bash
# render-config.sh — s6 oneshot 'render-config'. Runs once, before any daemon.
#
# Responsibilities (in order):
#   1. Resolve VAR__FILE Docker secrets into bare VAR (file wins only when the
#      bare var is unset/empty; a missing file is fatal).
#   2. Apply documented defaults for every optional variable.
#   3. Validate required variables (MAIL_HOSTNAME + the PG_* connection set).
#   4. envsubst every template listed in tpl/render.map into its target path,
#      then fix volume ownership and generate a self-signed cert if the
#      configured TLS files are absent.  (steps 4+ live in D.2)
#
# Test hook: RENDER_DUMP_ENV=1 prints the fully-resolved variable set to stdout
# (KEY=VALUE) and exits 0 *before* any filesystem write, so unit tests can
# assert on resolution without root or a populated rootfs.
set -euo pipefail

log()  { printf '[render-config] %s\n' "$*"; }
die()  { printf '[render-config] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 1. Resolve __FILE secrets ───────────────────────────────────────────────
# For each variable that may carry a Docker secret, a sibling VAR__FILE may
# point at a file whose (newline-trimmed) contents become VAR — but only when
# VAR itself is empty, so an explicit env value always wins. A VAR__FILE that
# names a missing/unreadable file is a hard error (fail fast, never silently
# start with no password).
SECRET_VARS="PG_PASSWORD PG_AUDIT_PASSWORD REDIS_PASSWORD RELAYHOST_PASSWORD MAIL_BOOTSTRAP_PASSWORD"
for var in $SECRET_VARS; do
    file_var="${var}__FILE"
    file_path="${!file_var:-}"
    [ -n "$file_path" ] || continue
    cur="${!var:-}"
    if [ -n "$cur" ]; then
        # Explicit bare value present — ignore the file, keep the env value.
        continue
    fi
    [ -r "$file_path" ] || die "${file_var}=${file_path} is not a readable file"
    # Strip a single trailing newline (the common 'echo secret > file' case).
    printf -v "$var" '%s' "$(cat "$file_path")"
    export "${var?}"
done

# ── 2. Defaults ──────────────────────────────────────────────────────────────
# Only fill when unset/empty so an explicit env value always wins.
set_default() {
    local name="$1" value="$2"
    if [ -z "${!name:-}" ]; then
        printf -v "$name" '%s' "$value"
        export "${name?}"
    fi
}
set_default PG_PORT             5432
set_default REDIS_PORT          6379
set_default REDIS_DB            0
set_default REDIS_PREFIX        mail
set_default CLAMAV_ENABLED      true
set_default CLAMAV_PORT         3310
set_default TLS_CERT_FILE       /tls/fullchain.pem
set_default TLS_KEY_FILE        /tls/privkey.pem
set_default PASSWORD_SCHEME     ARGON2ID
set_default ALLOW_WEAK_SCHEMES  false
set_default MESSAGE_SIZE_LIMIT  52428800
set_default RSPAMD_REJECT_SCORE 15
set_default DMARC_REPORT_ENABLED false
set_default AUDIT_ENABLED       true
set_default AUDIT_SCOPE         full
set_default POP3_ENABLED        false
set_default POSTSCREEN_ENABLED  true
set_default GREYLISTING_ENABLED true
# Audit DB creds fall back to the lookup-role creds when not given separately.
set_default PG_AUDIT_USER       "${PG_USER:-}"
set_default PG_AUDIT_PASSWORD   "${PG_PASSWORD:-}"

# ── 3. Validate required variables ───────────────────────────────────────────
REQUIRED_VARS="MAIL_HOSTNAME PG_HOST PG_PORT PG_DBNAME PG_USER PG_PASSWORD"
missing=""
for var in $REQUIRED_VARS; do
    [ -n "${!var:-}" ] || missing="${missing} ${var}"
done
[ -z "$missing" ] || die "required variable(s) not set:${missing}"

# ── Test hook: dump resolved env and stop before any filesystem write ────────
# Every variable referenced by a template plus everything validated above.
DUMP_VARS="MAIL_HOSTNAME PG_HOST PG_PORT PG_DBNAME PG_USER PG_PASSWORD \
  PG_AUDIT_USER PG_AUDIT_PASSWORD REDIS_HOST REDIS_PORT REDIS_DB REDIS_PREFIX \
  REDIS_PASSWORD CLAMAV_ENABLED CLAMAV_HOST CLAMAV_PORT TLS_CERT_FILE \
  TLS_KEY_FILE RELAYHOST RELAYHOST_USER RELAYHOST_PASSWORD PASSWORD_SCHEME \
  ALLOW_WEAK_SCHEMES MESSAGE_SIZE_LIMIT RSPAMD_REJECT_SCORE \
  DMARC_REPORT_ENABLED DMARC_REPORT_EMAIL AUDIT_ENABLED AUDIT_SCOPE \
  POP3_ENABLED POSTSCREEN_ENABLED GREYLISTING_ENABLED MAIL_BOOTSTRAP_DOMAIN \
  MAIL_BOOTSTRAP_ADMIN MAIL_BOOTSTRAP_PASSWORD"
if [ "${RENDER_DUMP_ENV:-}" = "1" ]; then
    for var in $DUMP_VARS; do
        printf '%s=%s\n' "$var" "${!var:-}"
    done
    exit 0
fi

log "env resolved"
# Steps 4+ (template render, ownership, self-signed TLS) are appended in D.2.
