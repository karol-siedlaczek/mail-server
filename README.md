# Mail server

Self-contained mail server appliance: **Postfix** (SMTP MX + authenticated
submission + forwarding), **Dovecot 2.4** (IMAP, LMTP, the single SASL auth
source, ManageSieve/Sieve) and **Rspamd** (spam scoring, DKIM signing,
SPF/DKIM/DMARC/ARC, ClamAV glue), supervised by **s6-overlay v3** on
`debian:13-slim`.

All mail data — `domains`, `users`, `forwardings`, `sender_login_maps`,
`audit_logs` — lives in an **external PostgreSQL** database the operator owns;
the image reaches it only through operator-editable SQL map files. Redis (Rspamd
state) and ClamAV (antivirus) are external too. Design rationale, the full
PostgreSQL schema and the mail-flow diagram are in
[`docs/superpowers/specs/2026-06-15-mail-server-image-design.md`](../../docs/superpowers/specs/2026-06-15-mail-server-image-design.md).

## Environment variables

> Documented per subsystem as each lands. The canonical list (required
> `MAIL_HOSTNAME`; Postgres, Redis, ClamAV, TLS, relayhost, password-scheme,
> audit and bootstrap groups; `__FILE` secret variants) is filled in by the
> configuration phase.

## Persistent volumes

| Path | Contents |
|------|----------|
| `/var/vmail` | delivered Maildirs (`<domain>/<localpart>/Maildir`) — back up |
| `/var/spool/postfix` | in-flight queue |
| `/var/lib/dovecot` | IMAP indexes |
| `/var/lib/rspamd` | Bayes/fuzzy + **DKIM/ARC private keys** — back up independently |
| `/tls` (ro) | mounted TLS certs |

## Development & tests

```bash
make -C images/mail-server build   # docker build -t mail-server:test
make -C images/mail-server lint    # shellcheck + compose config + sql syntax
make -C images/mail-server test    # unit + config-render tests (no daemons)
make -C images/mail-server itest   # integration: compose up + pytest
```

The test harness (`tests/compose.test.yml`, `tests/seed.sql`,
`tests/conftest.py`) brings up the built image against `postgres:16`, `redis:7`
and a catch-all SMTP sink; ClamAV is an optional `av` compose profile.

## Publishing

Tag `mail-server/v<semver>` to build and push (see the repository README).
Multi-arch `linux/amd64,linux/arm64`. No build variants.
