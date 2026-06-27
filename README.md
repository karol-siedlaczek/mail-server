# Mail server

A self-contained, container-native mail server: **SMTP** (inbound MX +
authenticated submission), **IMAP** (reading + server-side forwarding), spam
filtering, and **DKIM / SPF / DMARC / ARC**. Postfix + Dovecot 2.4 + Rspamd +
postsrsd, supervised by s6-overlay on `debian:13-slim`.

All mail data — `domains`, `users`, `forwardings`, `sender_login_maps`,
`audit_logs` — lives in an **external PostgreSQL** database you own; the image
reaches it only through operator-editable SQL maps under `sql/`. **Redis**
(Rspamd state) and **ClamAV** (antivirus) are external too. CRUD management is the
separate `mail-admin` companion image; this image ships only a non-interactive
first-boot bootstrap seed.

| Port | Service                                  |
|------|------------------------------------------|
| 25   | SMTP inbound (MX); no SASL, postscreen   |
| 587  | Submission (STARTTLS), SASL required     |
| 465  | Submission (implicit TLS), SASL required |
| 143  | IMAP (STARTTLS)                          |
| 993  | IMAP (implicit TLS)                      |
| 4190 | ManageSieve                              |

## Architecture

- **Postfix** — MTA. `postscreen` on :25, Rspamd milter on all paths, Dovecot
  SASL on submission only, `smtpd_sender_login_maps` (send-as) on submission
  only, forwarding via `virtual_alias_maps`, SRS via postsrsd.
- **Dovecot 2.4** — the single SASL auth authority (Postfix submission auths
  through it), IMAP, LMTP delivery to Maildir, ManageSieve, quota.
- **Rspamd** — milter: spam scoring, DKIM signing, SPF/DKIM/DMARC/ARC verify,
  ARC sealing, greylisting, ratelimiting, ClamAV glue. State in Redis.
- **postsrsd 1.x** — SRS envelope rewriting so forwarded bounces stay SPF-aligned.
- **audit-svc** — tiny Python service writing `audit_logs` rows (auth via the
  Dovecot auth-policy endpoint; send/delivery via maillog correlation).

Config is rendered from env at boot by the `render-config` s6 oneshot (`envsubst`
over `/tpl/*.tpl`). Every `VAR` may instead be supplied as `VAR__FILE=/path`
(Docker secret) — the file's contents become `VAR`.

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MAIL_HOSTNAME` | yes | — | FQDN for HELO/banner/SRS; must match the sending IP's PTR. |
| `PG_HOST` | yes | — | External Postgres host. |
| `PG_PORT` | no | `5432` | Postgres port. |
| `PG_DBNAME` | yes | — | Postgres database. |
| `PG_USER` | yes | — | `mail_ro` lookup role (Postfix maps + Dovecot auth). |
| `PG_PASSWORD` / `PG_PASSWORD__FILE` | yes | — | Password for `PG_USER`. |
| `PG_AUDIT_USER` | no | = `PG_USER` | `mail_audit` role (audit writes). |
| `PG_AUDIT_PASSWORD` / `PG_AUDIT_PASSWORD__FILE` | no | — | Password for `PG_AUDIT_USER`. |
| `REDIS_HOST` | yes | — | Redis host (Rspamd state). |
| `REDIS_PORT` | no | `6379` | Redis port. |
| `REDIS_DB` | no | `0` | Redis DB index. |
| `REDIS_PREFIX` | no | `mail` | Key-prefix so a shared Redis is namespaced. |
| `REDIS_USERNAME` | no | — | Redis 6+ ACL username. When set, login is `AUTH <user> <pass>`; unset = legacy password-only AUTH. |
| `REDIS_PASSWORD` / `REDIS_PASSWORD__FILE` | no | — | Redis AUTH password (or file). |
| `CLAMAV_ENABLED` | no | `true` | Enable the Rspamd antivirus module. |
| `CLAMAV_HOST` | no | — | ClamAV (`clamd`) host; AV disabled if unset. |
| `CLAMAV_PORT` | no | `3310` | ClamAV port. |
| `TLS_CERT_FILE` | no | `/tls/fullchain.pem` | Mounted cert chain (self-signed if absent). Ignored when `TLS_CHAIN_FILE` is set. |
| `TLS_KEY_FILE` | no | `/tls/privkey.pem` | Mounted private key. Ignored when `TLS_CHAIN_FILE` is set. |
| `TLS_CHAIN_FILE` | no | — | Single combined PEM (key + leaf + chain, **key first**) used for both Postfix and Dovecot. When set, overrides `TLS_CERT_FILE`/`TLS_KEY_FILE`. |
| `RELAYHOST` | no | — | Optional smarthost; direct outbound if unset. |
| `RELAYHOST_USER` | no | — | Smarthost SASL user. |
| `RELAYHOST_PASSWORD` / `RELAYHOST_PASSWORD__FILE` | no | — | Smarthost SASL password (or file). |
| `PASSWORD_SCHEME` | no | `ARGON2ID` | Hash scheme for new/bootstrap passwords. |
| `ALLOW_WEAK_SCHEMES` | no | `false` | `true` ONLY during `{MD5-CRYPT}` migration. |
| `MESSAGE_SIZE_LIMIT` | no | `52428800` | Max message bytes (50 MB). |
| `RSPAMD_REJECT_SCORE` | no | `15` | Rspamd reject threshold. |
| `DMARC_REPORT_ENABLED` | no | `false` | Send daily aggregate DMARC reports. |
| `DMARC_REPORT_EMAIL` | no | — | From/contact for aggregate reports. |
| `AUDIT_ENABLED` | no | `true` | Enable the `audit_logs` subsystem. |
| `AUDIT_SCOPE` | no | `full` | `full` = auth + send + delivery. |
| `POP3_ENABLED` | no | `false` | Expose POP3 110/995. |
| `POSTSCREEN_ENABLED` | no | `true` | postscreen on :25. |
| `GREYLISTING_ENABLED` | no | `true` | Rspamd greylisting (unauthenticated only). |
| `MAIL_BOOTSTRAP_DOMAIN` | no | — | First-boot: domain to seed (see Day 1). |
| `MAIL_BOOTSTRAP_ADMIN` | no | — | First-boot: admin mailbox (must be `@` the domain). |
| `MAIL_BOOTSTRAP_PASSWORD` / `MAIL_BOOTSTRAP_PASSWORD__FILE` | no | — | First-boot: admin password (hashed in-image). |

## Persistent volumes

| Path | Contents |
|------|----------|
| `/var/vmail` | Delivered Maildirs (`<domain>/<localpart>/Maildir`) — back up. |
| `/var/spool/postfix` | In-flight queue — must survive restart. |
| `/var/lib/dovecot` | IMAP indexes. |
| `/var/lib/rspamd` | Bayes/fuzzy + **DKIM/ARC private keys** — back up independently. |
| `/tls` (ro) | Mounted TLS cert + key. |

Losing `/var/lib/rspamd/dkim/*.key` silently breaks DKIM signing — back it up
separately from everything else.

## Usage (docker compose)

```yaml
services:
  mail:
    image: registry.siedlaczek.com.pl/mail-server:latest
    restart: unless-stopped
    hostname: mail.example.com
    ports:
      - "25:25"
      - "587:587"
      - "465:465"
      - "143:143"
      - "993:993"
      - "4190:4190"
    environment:
      MAIL_HOSTNAME: mail.example.com
      PG_HOST: postgres
      PG_DBNAME: mail
      PG_USER: mail_ro
      PG_PASSWORD__FILE: /run/secrets/pg_password
      PG_AUDIT_USER: mail_audit
      PG_AUDIT_PASSWORD__FILE: /run/secrets/pg_audit_password
      REDIS_HOST: redis
      REDIS_PREFIX: mail
      CLAMAV_HOST: clamav
      # First boot only — seed one domain + admin, then unset (see Day 1).
      MAIL_BOOTSTRAP_DOMAIN: example.com
      MAIL_BOOTSTRAP_ADMIN: admin@example.com
      MAIL_BOOTSTRAP_PASSWORD__FILE: /run/secrets/bootstrap_password
    secrets:
      - pg_password
      - pg_audit_password
      - bootstrap_password
    volumes:
      - mail-vmail:/var/vmail
      - mail-queue:/var/spool/postfix
      - mail-dovecot:/var/lib/dovecot
      - mail-rspamd:/var/lib/rspamd
      - /etc/letsencrypt/live/mail.example.com:/tls:ro
    healthcheck:
      test: ["CMD", "/usr/local/bin/healthcheck.sh"]
      start_period: 120s
      interval: 30s
      timeout: 10s

  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: mail
      POSTGRES_PASSWORD__FILE: /run/secrets/pg_superpass
    volumes:
      - pg-data:/var/lib/postgresql/data
      # Apply sql/schema.sql once; create mail_ro / mail_audit roles.
    secrets:
      - pg_superpass

  redis:
    image: redis:7
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis-data:/data

  clamav:
    image: clamav/clamav:latest
    restart: unless-stopped
    volumes:
      - clamav-db:/var/lib/clamav

secrets:
  pg_password: { file: ./secrets/pg_password }
  pg_audit_password: { file: ./secrets/pg_audit_password }
  pg_superpass: { file: ./secrets/pg_superpass }
  bootstrap_password: { file: ./secrets/bootstrap_password }

volumes:
  mail-vmail:
  mail-queue:
  mail-dovecot:
  mail-rspamd:
  pg-data:
  redis-data:
  clamav-db:
```

TLS: mount your Let's Encrypt chain at `/tls` (`fullchain.pem` + `privkey.pem`).
If the files are absent the image generates a self-signed pair so it still
starts — fine for testing, not for real delivery.

## Database setup

Before first boot, apply the schema and create the two least-privilege roles in
your Postgres:

```bash
psql "$DBURL" -f sql/schema.sql
psql "$DBURL" <<'SQL'
CREATE ROLE mail_ro    LOGIN PASSWORD '...';
CREATE ROLE mail_audit LOGIN PASSWORD '...';
GRANT SELECT ON domains, users, forwardings, sender_login_maps TO mail_ro;
GRANT INSERT ON audit_logs TO mail_audit;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO mail_audit;
SQL
```

> [!NOTE]
> The first-boot bootstrap (below) needs **INSERT on `domains` and `users`**. The
> `mail_ro` role is SELECT-only by design. For the seed either (a) point
> `PG_USER` at a role with temporary INSERT for the first boot, or (b) skip the
> bootstrap env and insert the domain + admin rows yourself. If `mail_ro` lacks
> INSERT, `mail-bootstrap` logs the privilege error and continues the boot — it
> never wedges the container.

## Day 1: bootstrap

The appliance is usable before `mail-admin` exists. On first boot, if
`MAIL_BOOTSTRAP_DOMAIN` is set **and the `domains` table is empty**, the
`mail-bootstrap` oneshot (after `render-config`, before the daemons):

1. inserts the domain (`dkim_selector` = `default`) and the admin mailbox, with
   the password hashed in-image via `doveadm pw -s ${PASSWORD_SCHEME}`,
2. generates the DKIM key at `/var/lib/rspamd/dkim/<domain>.default.key`,
3. prints the DKIM TXT and the other DNS records to publish.

It is **idempotent**: on any later boot the `domains` table is non-empty, so the
seed is a strict no-op (and the DKIM key is reused, never regenerated). After the
first successful boot, remove the `MAIL_BOOTSTRAP_*` env so the container logs
stay clean. Read the printed DNS block from the container logs:

```bash
docker compose logs mail | grep -A20 'DNS records'
```

## Required DNS records

For every domain you host (substitute `example.com`, your MX host, the DKIM TXT
printed by `mail-dkim-keygen`):

| Type | Name | Value |
|------|------|-------|
| A | `mail.example.com` | the server's static IPv4 (and `AAAA` for IPv6) |
| MX | `example.com` | `10 mail.example.com.` |
| SPF (TXT) | `example.com` | `v=spf1 mx -all` |
| DKIM (TXT) | `default._domainkey.example.com` | `v=DKIM1; k=rsa; p=<key>` (from `mail-dkim-keygen`) |
| DMARC (TXT) | `_dmarc.example.com` | `v=DMARC1; p=none; rua=mailto:postmaster@example.com` |
| MTA-STS (TXT) | `_mta-sts.example.com` | `v=STSv1; id=<timestamp>` |
| MTA-STS policy | `mta-sts.example.com` | HTTPS host serving `/.well-known/mta-sts.txt` (reverse proxy) |
| TLS-RPT (TXT) | `_smtp._tls.example.com` | `v=TLSRPTv1; rua=mailto:tlsrpt@example.com` |

Generate or print a domain's DKIM record any time:

```bash
docker compose exec mail mail-dkim-keygen example.com default
```

**DMARC rollout:** start `p=none`, observe aggregate reports 2–4 weeks, then
`p=quarantine`, then `p=reject`.

**MTA-STS policy file:** the HTTPS endpoint is **not** part of this image — your
reverse proxy serves it at `https://mta-sts.example.com/.well-known/mta-sts.txt`.
A starting policy is shipped at
[`rootfs/tpl/mta-sts.txt.sample`](rootfs/tpl/mta-sts.txt.sample) (begin with
`mode: testing`, switch to `mode: enforce` once TLS-RPT is clean). Bump the
`_mta-sts` TXT `id` whenever the policy file changes.

> [!IMPORTANT]
> **PTR / rDNS is a hard prerequisite this image cannot set.** The sending IP's
> PTR must equal `MAIL_HOSTNAME` (A = HELO = PTR). A residential/dynamic IP is on
> the Spamhaus PBL and will be rejected — use a static-IP VPS. DANE/TLSA only if
> the zone is DNSSEC-signed.

## Operations

- **Health:** `docker compose exec mail /usr/local/bin/healthcheck.sh` —
  aggregates `postfix status`, `doveadm service status`, `rspamadm control stat`,
  and (if configured) Redis `PING`. `start-period` ~120s for warm-up.
- **Queue:** `docker compose exec mail postqueue -p` (list) / `postqueue -f`
  (flush). Shutdown raises `S6_KILL_GRACETIME` (~20s) so the queue drains.
- **Config sanity:** `postfix check`, `doveconf -n`, `rspamadm configtest`.
- **Logs / audit:** daemon logs on container stdout; durable audit in the
  `audit_logs` table (query by `login`, `timestamp`, `queue_id`).
- **Add a domain/user later:** insert rows in Postgres (the future `mail-admin`,
  or SQL), then `mail-dkim-keygen <domain>` and publish the DKIM TXT. No restart
  needed for lookups; new DKIM keys are picked up on the next Rspamd reload.
- **Back up three things independently:** `/var/vmail`, the DKIM/ARC keys under
  `/var/lib/rspamd/dkim`, and the Postgres DB (`pg_dump`). Test restores.

## Migration from a legacy MySQL mail server

See [`docs/migration-mysql-to-postgres.md`](docs/migration-mysql-to-postgres.md)
for a `pgloader` recipe + post-load SQL that prefixes imported `md5crypt` hashes
with `{MD5-CRYPT}` and converts a flat `virtual` aliases file into `forwardings`
rows. Set `ALLOW_WEAK_SCHEMES=true` only while those legacy hashes are still in
use, then turn it off as users re-set passwords.

## Publishing

Tag `mail-server/v<semver>` to build and push (see the repository README). The
CI pipeline is unchanged: it builds and pushes a multi-arch
(`linux/amd64,linux/arm64`) image on any `mail-server/v*` tag. No build variants.

> [!IMPORTANT]
> Rspamd ≥3.13 has crashed with *Illegal instruction* (the SVE2 codepath) on some
> ARMv8 CPUs. The Dockerfile pins a known-good Rspamd version; before cutting a
> release, run the build-only multi-arch smoke and the integration suite so an
> arm64 regression is caught before the tag is pushed:

```bash
make -C images/mail-server buildx-smoke   # docker buildx --platform amd64,arm64 (build only)
make -C images/mail-server itest          # full happy-path e2e against compose

# Release: cut and push the tag (CI does the multi-arch build + push to the registry)
git tag mail-server/v0.1.0
git push origin mail-server/v0.1.0
```

For a tag like `mail-server/v0.1.0` the pipeline publishes `mail-server:0.1.0`,
`mail-server:0.1`, `mail-server:latest`, and `mail-server:<short-sha>` to
`registry.siedlaczek.com.pl`. To re-run a build, move and force-push the tag
(`git tag -f mail-server/v0.1.0 && git push -f origin mail-server/v0.1.0`).
