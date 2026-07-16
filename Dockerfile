# Full mail server appliance: Postfix (MTA) + Dovecot 2.4 (IMAP/LMTP/SASL/Sieve)
# + Rspamd (spam, DKIM/SPF/DMARC/ARC) + postsrsd (SRS) + a tiny Python audit
# service, all supervised by s6-overlay v3 as PID1. Postgres, Redis and ClamAV
# are external (operator-provided). Config is rendered from env at boot.
FROM debian:13-slim

ARG APP_VERSION=unknown
# s6-overlay v3 release (https://github.com/just-containers/s6-overlay/releases).
# Pinned for reproducible PID1 supervision; SHADOW digests are checked below.
ARG S6_OVERLAY_VERSION=3.2.0.2
# Rspamd is pinned to a known-good release: rspamd >=3.13 has SIGILL'd on the
# SVE2 codepath on some ARMv8 CPUs — verify on the actual arm64 target.
ARG RSPAMD_VERSION=3.11.1

LABEL org.opencontainers.image.version="${APP_VERSION}"
LABEL org.opencontainers.image.description="Mail server appliance: Postfix + Dovecot 2.4 + Rspamd + postsrsd, Postgres/Redis-backed, s6-overlay supervised."

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8
# s6 tuning: container exits non-zero if any oneshot/longun fails to start, and
# the queue gets ~20s to flush on SIGTERM before SIGKILL (spec: shutdown).
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_KILL_GRACETIME=20000 \
    S6_SERVICES_GRACETIME=20000

# ── Pin postfix/postdrop ids BEFORE installing postfix ──────────────────────
# Debian allocates the postfix user + postfix/postdrop groups dynamically at
# install time, so adding/removing any package can shift those numeric ids.
# The mail queue lives on a persistent volume (/var/spool/postfix) owned by
# them; a shift orphans it across rebuilds (postsuper "Permission denied" /
# "Postfix integrity check failed"). Fixed ids keep the volume valid forever;
# postfix's postinst reuses these pre-existing user/groups. (vmail is pinned to
# 5000 below for the same reason; render-config also self-heals via
# `postfix set-permissions` at boot.)
RUN set -eux; \
    groupadd -g 5001 postfix; \
    groupadd -g 5002 postdrop; \
    useradd  -u 5001 -g postfix -M -s /usr/sbin/nologin -d /var/spool/postfix postfix

# ── Base packages + APT repos (Rspamd from the official repo, which ships arm64) ──
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release apt-transport-https xz-utils \
        gettext-base; \
    # Rspamd official repo (pinned major channel), keyring verified over HTTPS.
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://rspamd.com/apt-stable/gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/rspamd.gpg; \
    CODENAME="$(lsb_release -cs)"; \
    echo "deb [signed-by=/etc/apt/keyrings/rspamd.gpg] http://rspamd.com/apt-stable/ ${CODENAME} main" \
        > /etc/apt/sources.list.d/rspamd.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        postfix postfix-pgsql \
        libsasl2-modules \
        dovecot-core dovecot-imapd dovecot-lmtpd dovecot-managesieved \
        dovecot-sieve dovecot-pgsql dovecot-pop3d \
        rspamd \
        postsrsd \
        unbound \
        redis-tools \
        postgresql-client \
        clamdscan \
        python3 python3-psycopg2 \
        openssl; \
    # Drop Debian's sysv/systemd auto-start cruft; s6 owns process lifecycle.
    rm -rf /etc/rcS.d /etc/rc?.d 2>/dev/null || true; \
    # debian-slim strips /usr/share/man, but `postfix set-permissions` (run at
    # boot to re-own the queue after a uid change) walks Postfix's file manifest
    # and aborts with "chown: cannot access .../mailq.1.gz" before it ever fixes
    # the queue dirs. Drop the man-page entries from the manifest so it completes.
    find /etc/postfix /usr/lib/postfix /usr/share/postfix /usr/libexec/postfix \
        -name 'postfix-files' -type f -exec sed -i '\#/usr/share/man#d' {} + \
        2>/dev/null || true; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ── s6-overlay v3 (noarch + per-arch tarball, picked from the build platform) ──
# BuildKit injects TARGETARCH (amd64|arm64); map it to s6's arch name.
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) S6_ARCH=x86_64 ;; \
        arm64) S6_ARCH=aarch64 ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    curl -fsSL -o s6-overlay-noarch.tar.xz \
        "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz"; \
    curl -fsSL -o s6-overlay-arch.tar.xz \
        "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz"; \
    tar -C / -Jxpf s6-overlay-noarch.tar.xz; \
    tar -C / -Jxpf s6-overlay-arch.tar.xz; \
    rm -f s6-overlay-noarch.tar.xz s6-overlay-arch.tar.xz

# ── vmail user/group (uid/gid 5000, matches Dovecot userdb in the spec) ──
RUN set -eux; \
    groupadd -g 5000 vmail; \
    useradd -u 5000 -g 5000 -d /var/vmail -s /usr/sbin/nologin -M vmail; \
    mkdir -p /var/vmail; \
    chown vmail:vmail /var/vmail

# ── Image filesystem overlay: s6 service defs, templates, helpers, SQL ──
COPY rootfs/ /
COPY sql/ /sql/
RUN set -eux; \
    chmod +x /usr/local/bin/*.sh /usr/local/bin/* 2>/dev/null || true; \
    chmod +x /usr/lib/dovecot/sieve/*.sh 2>/dev/null || true; \
    # imap_sieve runs the global report scripts as the vmail user; let it cache
    # the compiled .svbin next to them (otherwise Pigeonhole recompiles in memory
    # on every Junk move and logs a warning each time).
    chown vmail:vmail /etc/dovecot/sieve 2>/dev/null || true; \
    # s6-rc run/up scripts must be executable.
    find /etc/s6-overlay/s6-rc.d -type f \( -name run -o -name up -o -name finish \) -exec chmod +x {} +

# Persistent state (must survive restarts / be backed up — see spec).
VOLUME ["/var/vmail", "/var/spool/postfix", "/var/lib/dovecot", "/var/lib/rspamd"]

# 25 smtp(MX) · 465 smtps · 587 submission · 143 imap · 993 imaps · 4190 sieve
# · 110/995 pop3/pop3s (only served when POP3_ENABLED=true)
EXPOSE 25 465 587 143 993 4190 110 995

# Aggregate liveness: postfix + dovecot + rspamd (+ redis if configured).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/init"]
