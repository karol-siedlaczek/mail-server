# Mail server

A self-contained, container-native mail server: **SMTP** (inbound MX +
authenticated submission), **IMAP** (reading + server-side forwarding), spam
filtering, and **DKIM / SPF / DMARC / ARC**. Postfix + Dovecot 2.4 + Rspamd +
postsrsd, supervised by s6-overlay on `debian:13-slim`.

All mail data — `domains`, `users`, `forwardings`, `sender_login_maps`,
`audit_logs` — lives in an **external PostgreSQL** database you own; the image
reaches it only through operator-editable SQL maps under `sql/`. **Redis**
(Rspamd state) and **ClamAV** (antivirus) are external too. CRUD management is the
separate [`mail-controller`](https://github.com/karol-siedlaczek/mail-controller)
companion image; this image ships only a non-interactive first-boot bootstrap seed.

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
- **unbound** — localhost recursive resolver. DNSBLs refuse lookups via public
  resolvers (Docker's embedded DNS forwards to one), so it recurses public names
  directly and forwards the appliance's own backend hostnames to Docker's DNS.
  `render-config` writes the forward list and points `/etc/resolv.conf` at it.
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
| `PG_USER` | yes | — | `mail-server-ro_user` login user — granted the `mail-server-ro` role (Postfix maps + Dovecot auth). |
| `PG_PASSWORD` / `PG_PASSWORD__FILE` | yes | — | Password for `PG_USER`. |
| `PG_AUDIT_USER` | no | = `PG_USER` | `mail-server-audit_user` login user — granted the `mail-server-audit` role (audit writes). |
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
| `LOCAL_RESOLVER_ENABLED` | no | `true` | Run the in-container unbound resolver and point `/etc/resolv.conf` at it (so DNSBLs aren't queried via a public resolver). Set `false` to keep Docker's embedded DNS and supply your own recursor (see DNS resolver). |
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
      PG_USER: mail-server-ro_user
      PG_PASSWORD__FILE: /run/secrets/pg_password
      PG_AUDIT_USER: mail-server-audit_user
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
      # Apply sql/schema.sql once (creates the mail-server-ro / mail-server-audit
      # roles); then create the login users and grant them in (see Database setup).
    secrets:
      - pg_superpass

  redis:
    image: redis:8
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

Before first boot, apply the schema (it creates the least-privilege **roles**
`mail-server-ro` / `mail-server-audit` / `mail-server-admin`), then create the
login **users** and grant them the matching role:

```bash
psql "$DBURL" -f sql/schema.sql
psql "$DBURL" <<'SQL'
CREATE ROLE "mail-server-ro_user"    LOGIN PASSWORD '...';
CREATE ROLE "mail-server-audit_user" LOGIN PASSWORD '...';
GRANT "mail-server-ro"    TO "mail-server-ro_user";
GRANT "mail-server-audit" TO "mail-server-audit_user";
SQL
```

> [!NOTE]
> The first-boot bootstrap (below) needs **INSERT on `domains` and `users`**. The
> `mail-server-ro` role is SELECT-only by design. For the seed either (a) point
> `PG_USER` at a user with temporary INSERT for the first boot, or (b) skip the
> bootstrap env and insert the domain + admin rows yourself. If `mail-server-ro_user` lacks
> INSERT, `mail-bootstrap` logs the privilege error and continues the boot — it
> never wedges the container.

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

The DKIM TXT value comes from the domain's signing key — see
[DKIM keys](#dkim-keys) to generate, locate, or rotate it.

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
- **Add a domain/user later:** insert rows in Postgres (the future `mail-controller`,
  or SQL); lookups need no restart. Generate and publish its DKIM key per
  [DKIM keys](#dkim-keys).
- **Back up three things independently:** `/var/vmail`, the DKIM/ARC keys under
  `/var/lib/rspamd/dkim`, and the Postgres DB (`pg_dump`). Test restores.

### DKIM keys

Private signing keys live in the persistent Rspamd volume, one PEM per
domain/selector:

```
/var/lib/rspamd/dkim/<domain>.<selector>.key      # selector defaults to 'default'
```

List what exists:

```bash
docker compose exec mail ls -l /var/lib/rspamd/dkim/
```

If you booted **without** `MAIL_BOOTSTRAP_DOMAIN` (so the Day 1 seed was skipped),
this directory is empty and no signing key was generated — create one by hand:

```bash
docker compose exec mail mail-dkim-keygen example.com default
```

This writes `example.com.default.key` and prints the DNS TXT to publish at
`default._domainkey.example.com`. Then point the domain at the selector and
reload Rspamd so it signs with the new key:

1. set `domains.dkim_selector = 'default'` for the domain in Postgres (or via
   `mail-controller`),
2. `docker compose exec mail s6-svc -r /run/service/rspamd` (or restart the
   container).

`mail-dkim-keygen` refuses to overwrite a live key; to **rotate**, delete the
`.key` file deliberately, re-run it, and publish the new TXT. Losing these keys
silently breaks DKIM signing — back the directory up independently.

### DNS resolver

DNSBLs (Spamhaus, Barracuda, SpamCop) refuse lookups that arrive via large public
resolvers. Docker's embedded DNS forwards to one, so by default the image runs its
own **unbound** recursor on `127.0.0.1`: public names (DNSBL, MX) are resolved
recursively from the container, while the appliance's backend hostnames
(`PG_HOST`/`REDIS_HOST`/`CLAMAV_HOST`/`RELAYHOST`) are forwarded to Docker's DNS so
service discovery still works. This needs outbound port 53 to the internet.

To use your **own** resolver instead, set `LOCAL_RESOLVER_ENABLED=false` (the
unbound daemon then stays down and `/etc/resolv.conf` is left on Docker's DNS) and
point Docker's upstream at your recursor:

```yaml
services:
  mail:
    environment:
      LOCAL_RESOLVER_ENABLED: "false"
    dns:
      - 10.0.0.53   # your resolver — must RECURSE, not forward to 8.8.8.8/1.1.1.1,
                    # or Spamhaus sees the public resolver and blocks the query
```

Docker keeps `127.0.0.11` in the container (service names resolve) and forwards
external queries to your resolver. A forwarder that relays to a public resolver
defeats the purpose — it must do its own recursion.

## Day 1: Bootstrap

The appliance is usable before `mail-controller` exists. On first boot, if
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

## Publishing

Tag `v<semver>` to build and push. The CI pipeline
(`.github/workflows/docker-publish.yml`) builds and pushes a multi-arch
(`linux/amd64,linux/arm64`) image to `registry.siedlaczek.com.pl` on any `v*`
tag. No build variants.

> [!IMPORTANT]
> Rspamd ≥3.13 has crashed with *Illegal instruction* (the SVE2 codepath) on some
> ARMv8 CPUs. The Dockerfile pins a known-good Rspamd version; before cutting a
> release, run the build-only multi-arch smoke and the integration suite so an
> arm64 regression is caught before the tag is pushed:

```bash
make buildx-smoke   # docker buildx --platform amd64,arm64 (build only)
make itest          # full happy-path e2e against compose

# Release: cut and push the tag (CI does the multi-arch build + push to the registry)
git tag v0.1.0
git push origin v0.1.0
```

For a tag like `v0.1.0` the pipeline publishes `mail-server:0.1.0`,
`mail-server:0.1`, `mail-server:latest`, and `mail-server:<short-sha>` to
`registry.siedlaczek.com.pl`. To re-run a build, move and force-push the tag:
```bash
git tag -f v0.1.0
git push -f origin v0.1.0
