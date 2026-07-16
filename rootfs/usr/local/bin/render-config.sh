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
SECRET_VARS="PG_PASSWORD PG_AUDIT_PASSWORD REDIS_PASSWORD RELAYHOST_PASSWORD MAIL_BOOTSTRAP_PASSWORD RSPAMD_CONTROLLER_PASSWORD"
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
# In-container recursive resolver (unbound). Set false to keep Docker's embedded
# DNS and supply your own (e.g. a private recursor via compose `dns:`); when off
# the unbound daemon stays down and /etc/resolv.conf is left untouched.
set_default LOCAL_RESOLVER_ENABLED true
# Max redirect actions Sieve allows per script (Pigeonhole default is 4, checked
# at compile time against the whole generated forward script — too low for a
# fan-out alias). Raise as needed for aliases with many destinations.
set_default SIEVE_MAX_REDIRECTS 25
# Audit DB creds fall back to the lookup-role creds when not given separately.
set_default PG_AUDIT_USER       "${PG_USER:-}"
set_default PG_AUDIT_PASSWORD   "${PG_PASSWORD:-}"

# ── 2b. Derived variables ────────────────────────────────────────────────────
# SRS_DOMAIN: the bare domain (MAIL_HOSTNAME with the leading host label stripped).
# postsrsd 1.x receives this via -d flag; templates that need just the domain part
# reference ${SRS_DOMAIN}.  Example: mail.example.test -> example.test.
SRS_DOMAIN="${MAIL_HOSTNAME#*.}"
export SRS_DOMAIN
# Make SRS_DOMAIN visible to sibling s6 services. render-config's own `export`
# does not propagate to other services, but the postsrsd longrun needs it (it
# passes -d ${SRS_DOMAIN}); s6's with-contenv reads /run/s6/container_environment.
if [ -z "${RENDER_ROOT:-}" ] && [ -d /run/s6/container_environment ]; then
    printf '%s' "$SRS_DOMAIN" > /run/s6/container_environment/SRS_DOMAIN
fi

# ── 2c. Derived variables for Dovecot templates ──────────────────────────────
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

# ── 2c-redis. Redis authentication (Redis 6+ ACL) ────────────────────────────
# When REDIS_USERNAME is set, authenticate with `AUTH <user> <pass>` (Redis ACL);
# otherwise fall back to legacy password-only AUTH. The Rspamd redis module reads
# a static template, so we derive REDIS_USERNAME_LINE here: the full
# `username = "...";` directive when a username is set, or an empty string when
# it is not — an emitted `username = "";` would make Rspamd send AUTH "" <pass>
# and fail against an ACL-enabled Redis.
if [ -n "${REDIS_USERNAME:-}" ]; then
    REDIS_USERNAME_LINE="username = \"${REDIS_USERNAME}\";"$'\n'
else
    REDIS_USERNAME_LINE=""
fi
export REDIS_USERNAME REDIS_USERNAME_LINE

# ── 2c-tls. TLS file layout (single combined chain vs separate cert/key) ─────
# TLS_CHAIN_FILE (optional) is a single PEM holding the private key, the leaf
# cert and the issuer chain. When set it becomes the one source for both daemons:
#   - Postfix: smtpd_tls_chain_files points at it once (its native format).
#   - Dovecot: cert and key are both read from it.
# When unset, the historical split layout applies — TLS_CERT_FILE (fullchain) +
# TLS_KEY_FILE (key) — and Postfix lists the key first so the two halves form one
# ordered chain. POSTFIX_TLS_CHAIN_FILES is kept single-line (Postfix accepts a
# whitespace-separated list) so the RENDER_DUMP_ENV output stays one line/var.
if [ -n "${TLS_CHAIN_FILE:-}" ]; then
    POSTFIX_TLS_CHAIN_FILES="${TLS_CHAIN_FILE}"
    DOVECOT_SSL_CERT_FILE="${TLS_CHAIN_FILE}"
    DOVECOT_SSL_KEY_FILE="${TLS_CHAIN_FILE}"
else
    POSTFIX_TLS_CHAIN_FILES="${TLS_KEY_FILE} ${TLS_CERT_FILE}"
    DOVECOT_SSL_CERT_FILE="${TLS_CERT_FILE}"
    DOVECOT_SSL_KEY_FILE="${TLS_KEY_FILE}"
fi
export TLS_CHAIN_FILE POSTFIX_TLS_CHAIN_FILES DOVECOT_SSL_CERT_FILE DOVECOT_SSL_KEY_FILE

# ── 2c-relay. Smarthost SASL authentication ──────────────────────────────────
# When RELAYHOST_USER is set, Postfix authenticates to the smarthost (RELAYHOST)
# with SASL. Credentials go into a `static:` lookup — it returns the same
# user:password for every nexthop, which is exactly right for a single smarthost
# and needs no separately postmap'd file. `security_options = noanonymous` keeps
# the plaintext PLAIN/LOGIN mechanisms (what smarthosts use) enabled — Postfix's
# default `noplaintext` would reject them — while `smtp_tls_security_level` (may)
# still lets the creds travel inside TLS. POSTFIX_RELAYHOST_SASL is empty when no
# relay user is set, so direct-send / unauthenticated-relay setups render no SASL
# client lines at all. (The password must not contain whitespace: the static:
# value is parsed as a single main.cf token.)
if [ -n "${RELAYHOST_USER:-}" ]; then
    POSTFIX_RELAYHOST_SASL="smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = static:${RELAYHOST_USER}:${RELAYHOST_PASSWORD:-}
smtp_sasl_security_options = noanonymous
smtp_sasl_tls_security_options = noanonymous"
else
    POSTFIX_RELAYHOST_SASL=""
fi
export POSTFIX_RELAYHOST_SASL

# ── 2d. Derived variables for audit-svc (Dovecot auth-policy) ───────────────
# AUDIT_POLICY_NONCE: random per-deploy nonce for auth_policy_hash_nonce.
# Preserved across render-config re-runs when passed in from outside; generated
# once on first boot otherwise (the nonce only needs to be stable per container
# lifecycle, not across restarts).
AUDIT_POLICY_NONCE="${AUDIT_POLICY_NONCE:-$(head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
export AUDIT_POLICY_NONCE
# AUDIT_POLICY_BLOCK: the Dovecot auth_policy_* stanza, rendered from the
# _auth_policy_block.inc include, substituted into 10-auth.conf.tpl when
# AUDIT_ENABLED is truthy; empty string otherwise.
_SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
_AUTH_POLICY_INC="${_SELF_DIR}/../../../tpl/dovecot/_auth_policy_block.inc"
# In the container the templates live at /tpl (COPY rootfs/ /).
[ -f "$_AUTH_POLICY_INC" ] || _AUTH_POLICY_INC="/tpl/dovecot/_auth_policy_block.inc"
case "${AUDIT_ENABLED:-true}" in
    1|true|TRUE|True|yes|on)
        AUDIT_POLICY_BLOCK="$(AUDIT_POLICY_NONCE="$AUDIT_POLICY_NONCE" envsubst < "$_AUTH_POLICY_INC")" ;;
    *)
        AUDIT_POLICY_BLOCK="" ;;
esac
export AUDIT_POLICY_BLOCK

# ── 3. Validate required variables ───────────────────────────────────────────
REQUIRED_VARS="MAIL_HOSTNAME PG_HOST PG_PORT PG_DBNAME PG_USER PG_PASSWORD"
missing=""
for var in $REQUIRED_VARS; do
    [ -n "${!var:-}" ] || missing="${missing} ${var}"
done
[ -z "$missing" ] || die "required variable(s) not set:${missing}"

# ── Test hook: dump resolved env and stop before any filesystem write ────────
# Every variable referenced by a template plus everything validated above.
DUMP_VARS="MAIL_HOSTNAME SRS_DOMAIN PG_HOST PG_PORT PG_DBNAME PG_USER PG_PASSWORD \
  PG_AUDIT_USER PG_AUDIT_PASSWORD REDIS_HOST REDIS_PORT REDIS_DB REDIS_PREFIX \
  REDIS_USERNAME REDIS_USERNAME_LINE \
  REDIS_PASSWORD CLAMAV_ENABLED CLAMAV_HOST CLAMAV_PORT TLS_CERT_FILE \
  TLS_KEY_FILE TLS_CHAIN_FILE POSTFIX_TLS_CHAIN_FILES \
  DOVECOT_SSL_CERT_FILE DOVECOT_SSL_KEY_FILE \
  RELAYHOST RELAYHOST_USER RELAYHOST_PASSWORD POSTFIX_RELAYHOST_SASL PASSWORD_SCHEME \
  ALLOW_WEAK_SCHEMES MESSAGE_SIZE_LIMIT RSPAMD_REJECT_SCORE \
  DMARC_REPORT_ENABLED DMARC_REPORT_EMAIL AUDIT_ENABLED AUDIT_SCOPE \
  POP3_ENABLED POSTSCREEN_ENABLED GREYLISTING_ENABLED SIEVE_MAX_REDIRECTS \
  MAIL_BOOTSTRAP_DOMAIN \
  MAIL_BOOTSTRAP_ADMIN MAIL_BOOTSTRAP_PASSWORD \
  DOVECOT_PASSWORD_SCHEME DOVECOT_AUTH_ALLOW_WEAK \
  DOVECOT_POP3_PROTOCOLS DOVECOT_POP3_SERVICES \
  AUDIT_POLICY_BLOCK AUDIT_POLICY_NONCE"
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
# level higher (the repo-root sql/). We try rootfs/ first (container
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
        # Defensive: strip a trailing CR so a CRLF render.map never produces
        # dest paths (and rendered filenames) with an embedded '\r'.
        src="${src%$'\r'}"; dest="${dest%$'\r'}"
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

# ── 4b. Postfix :25 inbound — POSTSCREEN_ENABLED gate (Phase K) ──────────────
# master.cf.tpl carries the postscreen front-end on :25 by default. When
# POSTSCREEN_ENABLED=false, collapse back to a plain smtpd so port 25 still
# accepts mail (just without botnet pre-screening). The helper services
# (smtpd pass / tlsproxy / dnsblog) are then unused; strip them to keep
# `postfix check` clean.
gate_postscreen() {
    local mc="${RENDER_ROOT}/etc/postfix/master.cf"
    [ -f "$mc" ] || return 0
    case "${POSTSCREEN_ENABLED:-true}" in
        1|true|TRUE|True|yes|on)
            log "render-config: POSTSCREEN_ENABLED=true → :25 runs postscreen"
            ;;
        *)
            log "render-config: POSTSCREEN_ENABLED=false → :25 runs plain smtpd"
            sed -i \
                -e '/^# ── :25 inbound — fronted by postscreen/d' \
                -e '/^#.*render-config rewrites this back/d' \
                -e '/^smtpd     pass  /d' \
                -e '/^  -o smtpd_sasl_auth_enable=no$/d' \
                -e '/^tlsproxy  unix  .* tlsproxy$/d' \
                -e '/^dnsblog   unix  .* dnsblog$/d' \
                -e 's/^smtp      inet  n       -       n       -       1       postscreen$/smtp      inet  n       -       n       -       -       smtpd/' \
                "$mc"
            ;;
    esac
    log "rendered :25 inbound stanza (postscreen=${POSTSCREEN_ENABLED:-true})"
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
    # Single combined chain file (TLS_CHAIN_FILE) takes precedence: one PEM with
    # key + cert. Generate a self-signed pair into it (key first, so Postfix's
    # smtpd_tls_chain_files accepts it directly) only when the file is absent.
    if [ -n "${TLS_CHAIN_FILE:-}" ]; then
        local chain="${RENDER_ROOT}${TLS_CHAIN_FILE}"
        if [ -s "$chain" ]; then
            log "TLS chain file present, leaving untouched"
            return 0
        fi
        log "TLS chain file absent — generating a self-signed key+cert for ${MAIL_HOSTNAME}"
        mkdir -p "$(dirname "$chain")"
        local chain_cert="${chain}.crt"
        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$chain" -out "$chain_cert" -days 365 \
            -subj "/CN=${MAIL_HOSTNAME}" \
            -addext "subjectAltName=DNS:${MAIL_HOSTNAME}" >/dev/null 2>&1 \
            || die "self-signed certificate generation failed"
        cat "$chain_cert" >> "$chain"   # key (already written) followed by cert
        rm -f "$chain_cert"
        # The chain file holds the private key, so it must not be world-readable;
        # group-own it to postfix (Dovecot loads it as root) exactly like the
        # split-layout key below. Tests (RENDER_ROOT set) run unprivileged → 0600.
        if [ -z "${RENDER_ROOT}" ]; then
            chgrp postfix "$chain" 2>/dev/null || true
            chmod 640 "$chain"
        else
            chmod 600 "$chain"
        fi
        return 0
    fi
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

# ── 7. Postfix queue tree ────────────────────────────────────────────────────
# /var/spool/postfix is a VOLUME; on a fresh bind-mount / empty volume the queue
# directory tree (built into the image) is shadowed and `private/` does not
# exist. Dovecot binds its SASL/LMTP listener sockets into
# /var/spool/postfix/private/{auth,dovecot-lmtp} and does NOT depend on the
# postfix service, so without this it races ahead and dies with
# `bind(...) failed: No such file or directory` (ENOENT on the missing parent).
# `postfix set-permissions` (idempotent) creates the full tree with correct
# ownership; running it here — a oneshot both postfix and dovecot depend on —
# guarantees the dirs exist before either daemon starts. Skipped under
# RENDER_ROOT (tests run unprivileged and assert on rendered content only).
ensure_postfix_spool() {
    [ -z "$RENDER_ROOT" ] || return 0
    mkdir -p /var/spool/postfix
    # Dovecot binds its SASL/LMTP sockets into /var/spool/postfix/private/ and
    # must find that directory already present. The postfix master would
    # otherwise create it only once it starts, which races dovecot (dovecot has
    # no dependency on postfix). Create it here with postfix's canonical
    # ownership/mode so neither daemon is surprised.
    mkdir -p /var/spool/postfix/private
    chown postfix:root /var/spool/postfix/private 2>/dev/null || true
    chmod 0700 /var/spool/postfix/private
    # `postfix set-permissions` creates any missing queue directories AND (re)owns
    # the whole tree to the CURRENT postfix uid/gid. This matters across image
    # rebuilds: the persistent /var/spool/postfix volume keeps the old numeric
    # owner, so if the postfix uid shifts (e.g. a newly added package consumed an
    # id) the queue dirs become unreadable and postfix loops on
    # "postsuper: ... defer: Permission denied" / "Postfix integrity check failed".
    # Running it every boot self-heals that. Non-fatal (a warning must not wedge
    # boot) but its output is surfaced so real problems stay visible.
    local out
    out="$(postfix set-permissions 2>&1)" || true
    [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/[render-config]   postfix: /' >&2
    # Belt-and-suspenders: explicitly re-own the message-queue dirs to the postfix
    # user (group left as-is, modes already correct on the volume). These are what
    # postsuper opens during postfix's startup integrity check; a stale uid (from
    # an older image) makes it loop on "defer: Permission denied". chown is safe
    # and idempotent, and covers us even if set-permissions skips the live queue.
    local q
    for q in incoming active deferred defer bounce flush saved trace corrupt hold private; do
        [ -d "/var/spool/postfix/$q" ] && chown -R postfix "/var/spool/postfix/$q" 2>/dev/null || true
    done
    log "postfix queue tree ready under /var/spool/postfix"
}

# ── 8. Local resolver (unbound) ──────────────────────────────────────────────
# DNSBLs (Spamhaus et al.) refuse queries via public/shared resolvers; Docker's
# embedded DNS forwards to one, so every postscreen DNSBL lookup came back as the
# error code 127.255.255.254 instead of a verdict. We run unbound as a localhost
# RECURSING resolver (so DNSBL lookups originate from this container, not a public
# resolver) and point /etc/resolv.conf at it (swap_resolver, the very last step).
# The appliance's own backend hostnames must still resolve via Docker's embedded
# DNS, so render one forward-zone per configured host. Skipped under RENDER_ROOT.
render_unbound() {
    [ -z "$RENDER_ROOT" ] || return 0
    case "${LOCAL_RESOLVER_ENABLED:-true}" in
        0|false|FALSE|False|no|off)
            log "LOCAL_RESOLVER_ENABLED=false — in-container resolver off; keeping Docker DNS"
            return 0 ;;
    esac
    local fwd=/etc/unbound/unbound.conf.d/10-docker-forward.conf
    # Docker's embedded DNS == the nameserver currently in resolv.conf (we have
    # not swapped it yet). Memoise it so a re-run after the swap still finds it.
    local docker_dns
    docker_dns="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf 2>/dev/null)"
    if [ "${docker_dns:-}" = "127.0.0.1" ] || [ -z "${docker_dns:-}" ]; then
        docker_dns="$(cat /run/docker-dns 2>/dev/null || echo 127.0.0.11)"
    fi
    printf '%s\n' "$docker_dns" > /run/docker-dns 2>/dev/null || true
    {
        printf '# Rendered by render-config. Backend hostnames -> Docker DNS (%s).\n' "$docker_dns"
        for host in "${PG_HOST:-}" "${REDIS_HOST:-}" "${CLAMAV_HOST:-}" "${RELAYHOST:-}"; do
            [ -n "$host" ] || continue
            # Unwrap an optional [host] / [host]:port / host:port (RELAYHOST forms).
            host="${host#[}"; host="${host%%]*}"; host="${host%%:*}"
            # Skip IP literals — they need no DNS.
            case "$host" in
                *:*) continue ;;            # IPv6 literal
                *[!0-9.]*) : ;;             # has a non-(digit/dot) -> hostname
                *) continue ;;              # all digits/dots -> IPv4 literal
            esac
            printf 'forward-zone:\n  name: "%s."\n  forward-addr: %s\n' "$host" "$docker_dns"
        done
    } > "$fwd"
    log "unbound forward-zones rendered (backend names -> ${docker_dns})"
}

# Point the system resolver at unbound. LAST step on purpose: render-config's own
# psql (DKIM maps, below) must still use Docker's DNS, and the unbound longrun
# depends on render-config while every DNS-using service depends on unbound, so by
# the time anything resolves through 127.0.0.1 unbound is up. Non-fatal: if
# resolv.conf is read-only we keep Docker's DNS (DNSBL degraded, mail still flows).
swap_resolver() {
    [ -z "$RENDER_ROOT" ] || return 0
    case "${LOCAL_RESOLVER_ENABLED:-true}" in
        0|false|FALSE|False|no|off) return 0 ;;
    esac
    local keep
    keep="$(awk '/^search|^domain/' /etc/resolv.conf 2>/dev/null)"
    if {
        printf 'nameserver 127.0.0.1\n'
        [ -n "$keep" ] && printf '%s\n' "$keep"
        printf 'options timeout:2 attempts:2\n'
    } > /etc/resolv.conf 2>/dev/null; then
        log "resolv.conf now points at the local unbound resolver"
    else
        log "WARNING: could not rewrite /etc/resolv.conf; keeping Docker DNS (DNSBL may stay blocked)"
    fi
}

log "env resolved; rendering templates"
render_templates
gate_postscreen
ensure_tls
fix_ownership
ensure_postfix_spool
render_unbound

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

# Restrict envsubst to the same variable set used by render_templates() so that
# bare '$' characters in future Rspamd config syntax are never accidentally
# substituted (mirrors the pattern established in render_templates above).
rspamd_subst_vars="$(printf '${%s} ' $DUMP_VARS)"

# Render every Rspamd template except antivirus (gated below).
for tpl in "$rspamd_src"/*.tpl; do
  [ -f "$tpl" ] || continue  # skip if glob expanded to a literal (no matches)
  name="$(basename "${tpl%.tpl}")"
  [ "$name" = "antivirus.conf" ] && continue
  envsubst "$rspamd_subst_vars" < "$tpl" > "$RSPAMD_LOCALD_DIR/$name"
done

# ClamAV antivirus: render the enabled template only when switched on AND a host
# is set AND the template exists; otherwise write a clean disabled stanza so the
# module never tries to dial a missing clamd.
case "${CLAMAV_ENABLED}" in 1|true|TRUE|True|yes|on) clamav_on=1 ;; *) clamav_on=0 ;; esac
if [ "$clamav_on" = 1 ] && [ -n "${CLAMAV_HOST:-}" ] && [ -f "$rspamd_src/antivirus.conf.tpl" ]; then
  envsubst "$rspamd_subst_vars" < "$rspamd_src/antivirus.conf.tpl" > "$RSPAMD_LOCALD_DIR/antivirus.conf"
else
  # Disable the whole antivirus module with a top-level flag. rspamd 4.x treats
  # `clamav { ... }` as an AV *rule* definition and fails configtest ("cannot
  # add AV rule: clamav") when clamd is unreachable; a top-level `enabled =
  # false;` switches the module off cleanly with no rule registered.
  printf 'enabled = false;\n' \
    > "$RSPAMD_LOCALD_DIR/antivirus.conf"
fi

# --- GREYLISTING_ENABLED gate (Phase K) --------------------------------------
# Greylisting is the default. When disabled, overwrite the rendered config with
# a minimal disable stub so Rspamd loads cleanly but performs no greylisting.
case "${GREYLISTING_ENABLED:-true}" in
    1|true|TRUE|True|yes|on) ;;
    *)
        log "render-config: GREYLISTING_ENABLED=false → greylisting off"
        printf 'enabled = false;\n' > "$RSPAMD_LOCALD_DIR/greylist.conf"
        ;;
esac

# --- Controller bind (HAProxy backend) ---------------------------------------
# The Rspamd controller (web UI + HTTP API on :11334) is localhost-only by
# default. Expose it on all interfaces — so a reverse proxy / load balancer
# (e.g. HAProxy on the same Docker network) can reach it — ONLY when a password
# is configured, so an unauthenticated controller is never opened to the network.
# RSPAMD_CONTROLLER_PASSWORD may be plaintext (hashed here with `rspamadm pw`,
# mirroring how mail-bootstrap uses `doveadm pw`) or an already-hashed value
# (starts with '$'), which is injected verbatim (also lets tests exercise the
# exposed path without rspamadm). Without a usable hash we stay localhost-only.
_ctrl="$RSPAMD_LOCALD_DIR/worker-controller.inc"
_cpw="${RSPAMD_CONTROLLER_PASSWORD:-}"
if [ -n "$_cpw" ]; then
    case "$_cpw" in
        \$*) _chash="$_cpw" ;;                       # already an rspamadm hash
        *)   if command -v rspamadm >/dev/null 2>&1; then
                 _chash="$(rspamadm pw -q -p "$_cpw" 2>/dev/null || true)"
             else
                 _chash=""
             fi ;;
    esac
    if [ -n "$_chash" ]; then
        {
            printf 'bind_socket = "*:11334";\n'
            printf 'password = "%s";\n' "$_chash"
            printf 'enable_password = "%s";\n' "$_chash"
            # Trust loopback for enable-level commands (Sieve learn-on-move runs
            # `rspamc learn_*` locally); HAProxy/remote still needs the password.
            printf 'secure_ip = "127.0.0.1";\n'
            printf 'secure_ip = "::1";\n'
        } > "$_ctrl"
        log "rspamd controller: bound *:11334 with password (HAProxy backend)"
    else
        printf 'bind_socket = "127.0.0.1:11334";\n' > "$_ctrl"
        log "WARNING: RSPAMD_CONTROLLER_PASSWORD set but could not hash it (rspamadm missing?); controller stays localhost-only"
    fi
else
    printf 'bind_socket = "127.0.0.1:11334";\n' > "$_ctrl"
fi

# DKIM/ARC maps: domain->selector and domain->keypath, from the active domains.
# RSPAMD_DKIM_ROWS (newline-separated "domain selector") lets tests inject rows
# without a live DB; otherwise query Postgres with the mail-server-ro role.
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

# Last: repoint the system resolver at unbound (after the psql above, which must
# still resolve PG_HOST via Docker's DNS).
swap_resolver

log "configuration rendered"
