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
    # Strip ALL trailing newlines (shell $() substitution behaviour; the common
    # 'echo secret > file' case has exactly one, but we strip any number).
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

# ── 2b. Derived variables for Dovecot templates ──────────────────────────────
# These are computed from the primary env knobs above and passed to envsubst.
# DOVECOT_PASSWORD_SCHEME: the scheme name passed to passdb_default_password_scheme.
DOVECOT_PASSWORD_SCHEME="${PASSWORD_SCHEME:-ARGON2ID}"
export DOVECOT_PASSWORD_SCHEME
# DOVECOT_AUTH_ALLOW_WEAK: Dovecot 'yes'/'no' form of ALLOW_WEAK_SCHEMES.
if [ "${ALLOW_WEAK_SCHEMES:-false}" = "true" ]; then
    DOVECOT_AUTH_ALLOW_WEAK="yes"
else
    DOVECOT_AUTH_ALLOW_WEAK="no"
fi
export DOVECOT_AUTH_ALLOW_WEAK
# DOVECOT_POP3_PROTOCOLS / DOVECOT_POP3_SERVICES: extra protocol/service lines
# appended to the protocols directive and service block when POP3 is enabled.
if [ "${POP3_ENABLED:-false}" = "true" ]; then
    DOVECOT_POP3_PROTOCOLS=" pop3"
    DOVECOT_POP3_SERVICES='service pop3-login {
  inet_listener pop3 {
    port = 110
  }
  inet_listener pop3s {
    port = 995
    ssl = yes
  }
}'
else
    DOVECOT_POP3_PROTOCOLS=""
    DOVECOT_POP3_SERVICES=""
fi
export DOVECOT_POP3_PROTOCOLS DOVECOT_POP3_SERVICES

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
  MAIL_BOOTSTRAP_ADMIN MAIL_BOOTSTRAP_PASSWORD \
  DOVECOT_PASSWORD_SCHEME DOVECOT_AUTH_ALLOW_WEAK \
  DOVECOT_POP3_PROTOCOLS DOVECOT_POP3_SERVICES"
if [ "${RENDER_DUMP_ENV:-}" = "1" ]; then
    for var in $DUMP_VARS; do
        printf '%s=%s\n' "$var" "${!var:-}"
    done
    exit 0
fi

# ── 4. Template render loop ──────────────────────────────────────────────────
# RENDER_ROOT (tests only) prefixes every absolute dest so a full render can
# happen in an unprivileged tmpdir. Empty in the container → real paths.
RENDER_ROOT="${RENDER_ROOT:-}"
# Resolve the dir that contains both tpl/ and sql/. In the container this is
# '/' (script at /usr/local/bin, COPY rootfs/ / + COPY sql/ /sql/ put both
# there); under test SELF is the image's rootfs/ checkout but sql/ lives one
# level higher (images/mail-server/sql/). We try rootfs/ first (container
# path), then fall back one level for the test environment.
SELF="$(cd "$(dirname "$0")/../../.." && pwd)"
if [ ! -d "${SELF}/sql" ] && [ -d "${SELF}/../sql" ]; then
    # Test environment: script is inside rootfs/, sql/ is a sibling of rootfs/.
    # Keep SELF pointing to rootfs/ for tpl/render.map, but expose a SQL_ROOT
    # that actually resolves to the sql/ directory.
    SQL_ROOT="$(cd "${SELF}/../sql" && pwd)"
else
    SQL_ROOT="${SELF}/sql"
fi
RENDER_MAP="${SELF}/tpl/render.map"

render_templates() {
    [ -r "$RENDER_MAP" ] || die "render map not found: $RENDER_MAP"
    # Vars envsubst is allowed to substitute (restricting the set means a bare
    # '$' in a config — e.g. a regex — is left untouched unless we name it).
    local subst_vars
    subst_vars="$(printf '${%s} ' $DUMP_VARS)"
    local src dest abs_dest
    while read -r src dest _rest; do
        case "$src" in ''|'#'*) continue ;; esac
        [ -n "$dest" ] || die "render.map entry for '$src' has no dest"
        # sql/ may live outside rootfs/ (test env); use SQL_ROOT for sql/ paths.
        local src_path
        case "$src" in
            sql/*) src_path="${SQL_ROOT}/${src#sql/}" ;;
            *)     src_path="${SELF}/${src}" ;;
        esac
        [ -r "$src_path" ] || die "template missing: $src_path"
        abs_dest="${RENDER_ROOT}${dest}"
        mkdir -p "$(dirname "$abs_dest")"
        envsubst "$subst_vars" < "$src_path" > "$abs_dest"
        log "rendered ${src} -> ${dest}"
    done < "$RENDER_MAP"
}

# ── 5. Volume ownership ──────────────────────────────────────────────────────
# Mail store + daemon state must be owned by the vmail/runtime uids. Skipped
# under RENDER_ROOT (tests run unprivileged and assert on content, not chown).
fix_ownership() {
    [ -z "$RENDER_ROOT" ] || return 0
    # uid/gid 5000 == vmail (matches Dovecot userdb in the spec).
    for d in /var/vmail /var/lib/dovecot; do
        [ -d "$d" ] && chown -R 5000:5000 "$d" || true
    done
    [ -d /var/lib/rspamd ] && chown -R _rspamd:_rspamd /var/lib/rspamd || true
    return 0
}

# ── 6. Self-signed TLS fallback ──────────────────────────────────────────────
# Only when the configured cert/key are absent, so a real mounted LE cert is
# never clobbered. Lets the container boot for tests without a CA.
ensure_tls() {
    local cert="${RENDER_ROOT}${TLS_CERT_FILE}"
    local key="${RENDER_ROOT}${TLS_KEY_FILE}"
    if [ -s "$cert" ] && [ -s "$key" ]; then
        log "TLS cert/key present, leaving untouched"
        return 0
    fi
    log "TLS cert/key absent — generating a self-signed pair for ${MAIL_HOSTNAME}"
    mkdir -p "$(dirname "$cert")" "$(dirname "$key")"
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$key" -out "$cert" -days 365 \
        -subj "/CN=${MAIL_HOSTNAME}" \
        -addext "subjectAltName=DNS:${MAIL_HOSTNAME}" >/dev/null 2>&1 \
        || die "self-signed certificate generation failed"
    chmod 644 "$cert"
    # Postfix smtpd runs as the unprivileged 'postfix' user and must be able to
    # read the key, so group-own it to 'postfix' and make it group-readable
    # (Dovecot loads it as root). Skipped under RENDER_ROOT: tests run
    # unprivileged and don't assert perms, so keep the key 0600 there.
    if [ -z "${RENDER_ROOT}" ]; then
        chgrp postfix "$key" 2>/dev/null || true
        chmod 640 "$key"
    else
        chmod 600 "$key"
    fi
}

log "env resolved; rendering templates"
render_templates
ensure_tls
fix_ownership

# ── Rspamd ──────────────────────────────────────────────────────────────────
# Output roots (overridable for tests).
# Defaults honour RENDER_ROOT so full_render() tests in test_render.py never
# try to mkdir /etc/rspamd (permission-denied on the host); inside the running
# container RENDER_ROOT is empty and the real /etc/rspamd/local.d is used.
: "${RSPAMD_LOCALD_DIR:=${RENDER_ROOT}/etc/rspamd/local.d}"
: "${RSPAMD_DKIM_DIR:=${RENDER_ROOT}/etc/rspamd/dkim}"
: "${RSPAMD_SKIP_DB:=0}"
: "${RSPAMD_REJECT_SCORE:=15}"
: "${REDIS_PORT:=6379}"
: "${REDIS_DB:=0}"
: "${REDIS_PREFIX:=mail}"
: "${CLAMAV_ENABLED:=true}"
: "${CLAMAV_PORT:=3310}"
: "${DMARC_REPORT_ENABLED:=false}"
mkdir -p "$RSPAMD_LOCALD_DIR" "$RSPAMD_DKIM_DIR"

# Normalise DMARC_REPORT_ENABLED to a UCL boolean literal for the template.
case "${DMARC_REPORT_ENABLED}" in
  1|true|TRUE|True|yes|on) DMARC_REPORT_ENABLED=true ;;
  *)                       DMARC_REPORT_ENABLED=false ;;
esac
export DMARC_REPORT_ENABLED

rspamd_src="$(cd "$(dirname "$0")/../../../tpl/rspamd/local.d" 2>/dev/null && pwd)" || rspamd_src=""
# In the running image templates live at /tpl; fall back to that.
[ -n "$rspamd_src" ] && [ -d "$rspamd_src" ] || rspamd_src="/tpl/rspamd/local.d"

# Render every Rspamd template except antivirus (gated below).
for tpl in "$rspamd_src"/*.tpl; do
  [ -f "$tpl" ] || continue  # skip if glob expanded to a literal (no matches)
  name="$(basename "${tpl%.tpl}")"
  [ "$name" = "antivirus.conf" ] && continue
  envsubst < "$tpl" > "$RSPAMD_LOCALD_DIR/$name"
done

# ClamAV antivirus: render the enabled template only when switched on AND a host
# is set AND the template exists; otherwise write a clean disabled stanza so the
# module never tries to dial a missing clamd.
case "${CLAMAV_ENABLED}" in 1|true|TRUE|True|yes|on) clamav_on=1 ;; *) clamav_on=0 ;; esac
if [ "$clamav_on" = 1 ] && [ -n "${CLAMAV_HOST:-}" ] && [ -f "$rspamd_src/antivirus.conf.tpl" ]; then
  envsubst < "$rspamd_src/antivirus.conf.tpl" > "$RSPAMD_LOCALD_DIR/antivirus.conf"
else
  printf 'clamav { enabled = false; }\n' > "$RSPAMD_LOCALD_DIR/antivirus.conf"
fi

# DKIM/ARC maps: domain->selector and domain->keypath, from the active domains.
# RSPAMD_DKIM_ROWS (newline-separated "domain selector") lets tests inject rows
# without a live DB; otherwise query Postgres with the mail_ro role.
render_dkim_maps() {
  : > "$RSPAMD_DKIM_DIR/selectors.map"
  : > "$RSPAMD_DKIM_DIR/paths.map"
  while read -r dom sel; do
    [ -z "$dom" ] && continue
    [ -z "$sel" ] && sel=default
    printf '%s %s\n' "$dom" "$sel" >> "$RSPAMD_DKIM_DIR/selectors.map"
    printf '%s /var/lib/rspamd/dkim/%s.%s.key\n' "$dom" "$dom" "$sel" \
      >> "$RSPAMD_DKIM_DIR/paths.map"
  done
}

if [ -n "${RSPAMD_DKIM_ROWS:-}" ]; then
  printf '%s\n' "$RSPAMD_DKIM_ROWS" | render_dkim_maps
elif [ "${RSPAMD_SKIP_DB}" = "1" ]; then
  : > "$RSPAMD_DKIM_DIR/selectors.map"
  : > "$RSPAMD_DKIM_DIR/paths.map"
else
  PGPASSWORD="${PG_PASSWORD}" psql -tA -F' ' \
    -h "${PG_HOST}" -p "${PG_PORT:-5432}" -U "${PG_USER}" -d "${PG_DBNAME}" \
    -c "SELECT domain, dkim_selector FROM domains WHERE active" \
    | render_dkim_maps
fi

log "configuration rendered"
