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
| 110  | POP3 (STARTTLS) — only if `POP3_ENABLED` |
| 995  | POP3 (implicit TLS) — only if `POP3_ENABLED` |

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
  Dovecot auth-policy endpoint; send/delivery via maillog correlation). Which
  rows it writes is gated by `AUDIT_SCOPE`.
- **sieve-forward-sync** — Python longrun that regenerates the global Sieve
  forward script from the `forwardings` table and keeps it current via Postgres
  `LISTEN/NOTIFY` (with a ~60s fallback resync). It writes the spam-gated forward
  rules the Dovecot `forward` script executes at LMTP delivery.
- **dmarc-report** — longrun that acts as a daily cron: once per day (at
  `DMARC_REPORT_HOUR` UTC) it runs `rspamadm dmarc_report` to send the previous
  day's aggregate DMARC report. Idles unless `DMARC_REPORT_ENABLED=true`.

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
| `PG_AUDIT_PASSWORD` / `PG_AUDIT_PASSWORD__FILE` | no | = `PG_PASSWORD` | Password for `PG_AUDIT_USER`. Falls back to `PG_PASSWORD` when unset (so a single-user setup needs neither audit var). |
| `PG_WAIT_TIMEOUT` | no | `60` | Seconds the `postgres-ready` oneshot waits for Postgres on first boot before proceeding anyway (best-effort; audit-svc reconnects on its own). |
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
| `RELAYHOST` | no | — | Optional smarthost (e.g. `[smtp.provider.com]:587`); direct outbound if unset. |
| `RELAYHOST_USER` | no | — | Smarthost SASL user. When set, Postfix authenticates to the smarthost (`smtp_sasl_auth_enable=yes`); unset → the relay is used without auth. |
| `RELAYHOST_PASSWORD` / `RELAYHOST_PASSWORD__FILE` | no | — | Smarthost SASL password (or file). Must not contain whitespace (it becomes a single `static:` map token). Ensure the smarthost offers STARTTLS so the credentials travel encrypted. |
| `PASSWORD_SCHEME` | no | `ARGON2ID` | Hash scheme for new/bootstrap passwords. |
| `ALLOW_WEAK_SCHEMES` | no | `false` | `true` ONLY during `{MD5-CRYPT}` migration. |
| `MESSAGE_SIZE_LIMIT` | no | `52428800` | Max message bytes (50 MB). |
| `RSPAMD_REJECT_SCORE` | no | `15` | Rspamd reject threshold. |
| `RSPAMD_CONTROLLER_PASSWORD` / `RSPAMD_CONTROLLER_PASSWORD__FILE` | no | — | When set, the Rspamd controller (web UI + API) binds `*:11334` (reachable by a reverse proxy / HAProxy on the network) and requires this password; plaintext is hashed at boot, or pass an `rspamadm pw` hash. Unset → controller stays `127.0.0.1:11334`. |
| `DMARC_REPORT_ENABLED` | no | `false` | Send daily aggregate DMARC reports. |
| `DMARC_REPORT_EMAIL` | no | — | From/contact for aggregate reports. |
| `DMARC_REPORT_HOUR` | no | `3` | Hour (UTC, 0–23) at which the daily aggregate report run fires. Only relevant when `DMARC_REPORT_ENABLED=true`. |
| `AUDIT_ENABLED` | no | `true` | Enable the `audit_logs` subsystem. |
| `AUDIT_SCOPE` | no | `full` | Which event kinds `audit-svc` records. `full`/`all` = `auth` + `send` + `delivery`; or a comma/space-separated subset, e.g. `auth` or `auth,delivery`. Unknown/empty → `full` (fails open). Requires `AUDIT_ENABLED=true`. |
| `POP3_ENABLED` | no | `false` | Expose POP3 110/995. |
| `POSTSCREEN_ENABLED` | no | `true` | postscreen on :25. |
| `GREYLISTING_ENABLED` | no | `true` | Rspamd greylisting (unauthenticated only). |
| `SIEVE_MAX_REDIRECTS` | no | `25` | Max `redirect` actions Sieve allows per script. Pigeonhole's default (4) is too low for fan-out aliases and, if exceeded, fails the whole generated forward script at compile time (disabling all forwarding). Raise for aliases with many destinations. |
| `LOCAL_RESOLVER_ENABLED` | no | `true` | Run the in-container unbound resolver and point `/etc/resolv.conf` at it (so DNSBLs aren't queried via a public resolver). Set `false` to keep Docker's embedded DNS and supply your own recursor (see DNS resolver). |
| `DKIM_KEY_BITS` | no | `2048` | RSA key size `mail-dkim-keygen` (and the Day 1 seed) generate. |
| `MAIL_FUZZY_FLAG` | no | `1` | Fuzzy flag `mail-learn-spam` stores a fingerprint under — must match the `local` rule's flag in `fuzzy_check.conf`. |
| `MAIL_FUZZY_WEIGHT` | no | `10` | Weight `mail-learn-spam` gives a stored fuzzy hash (how strongly a later match counts). |
| `MAIL_RELEASE_ENVELOPE_FROM` | no | `postmaster@$MAIL_HOSTNAME` | Envelope-sender `mail-release` uses when re-injecting a released false positive (so bounces return to you, not the original sender). |
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
  mail-server:
    image: registry.siedlaczek.com.pl/mail-server:latest
    restart: unless-stopped
    hostname: mail.example.com
    ports:
      - "0.0.0.0:25:25/tcp"     # SMTP inbound, postscreen
      - "0.0.0.0:587:587/tcp"   # SMTP (STARTTLS)
      - "0.0.0.0:465:465/tcp"   # SMTP (SSL)
      - "0.0.0.0:143:143/tcp"   # IMAP (STARTTLS)
      - "0.0.0.0:993:993/tcp"   # IMAP (implict TLS)
      - "0.0.0.0:4190:4190/tcp" # ManageSieve
      - "0.0.0.0:110:110/tcp"   # POP3 (STARTTLS) - only if POP3_ENABLED: true
      - "0.0.0.0:995:995/tcp"   # POP3 (implicit TLS)- only if POP3_ENABLED: true
    environment:
      TZ: Europe/Warsaw
      MAIL_HOSTNAME: mail.example.com
      AUDIT_ENABLED: "true"
      AUDIT_SCOPE: "full"
      POP3_ENABLED: "true"
      # --- PostgreSQL ---
      PG_HOST: postgres
      PG_DBNAME: mail-server
      PG_USER: mail-server-ro_user
      PG_PASSWORD__FILE: /run/secrets/pg_password # Or by PG_PASSWORD
      PG_AUDIT_USER: mail-server-audit_user
      PG_AUDIT_PASSWORD__FILE: /run/secrets/pg_audit_password # or by PG_AUDIT_PASSWORD
      # --- Redis ---
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_DB: "0"
      REDIS_USERNAME: "mail-server_user"
      REDIS_PASSWORD: "<PASS>" # Or by REDIS_PASSWORD_FILE
      REDIS_PREFIX: mail-server
      # --- ClamAV ---
      CLAMAV_ENABLED: "true"
      CLAMAV_HOST: clamav
      CLAMAV_PORT: "3310"
      # --- TLS ---
      TLS_CERT_FILE: "/tls/cert.pem"
      TLS_KEY_FILE: "/tls/privkey.pem"
      # --- DMARC ---
      DMARC_REPORT_ENABLED: "true"
      DMARC_REPORT_EMAIL: "dmarc-reports@example.com"
      DMARC_REPORT_HOUR: "3"
      # --- Mail tuning ---
      MESSAGE_SIZE_LIMIT: "52428800" # 50MB
      RSPAMD_REJECT_SCORE: "15"
      RSPAMD_CONTROLLER_PASSWORD: "<PASS>"
      POSTSCREEN_ENABLED: "true"
      GREYLISTING_ENABLED: "true"
      LOCAL_RESOLVER_ENABLED: "true"
      SIEVE_MAX_REDIRECTS: "25"
      # --- Bootstrap ---
      MAIL_BOOTSTRAP_DOMAIN: example.com
      MAIL_BOOTSTRAP_ADMIN: admin@example.com
      MAIL_BOOTSTRAP_PASSWORD__FILE: /run/secrets/bootstrap_password
    secrets:
      - pg_password
      - pg_audit_password
      - bootstrap_password
    volumes:
      - /opt/mail-server/vmail:/var/vmail
      - /opt/mail-server/queue:/var/spool/postfix
      - /opt/mail-server/dovecot:/var/lib/dovecot
      - /opt/mail-server/rspamd:/var/lib/rspamd
      - /opt/mail-server/tls:/tls:ro
    healthcheck:
      test: ["CMD", "/usr/local/bin/healthcheck.sh"]
      interval: 30s
      timeout: 10s
      start_period: 120s

secrets:
  pg_password: { file: ./secrets/pg_password }
  pg_audit_password: { file: ./secrets/pg_audit_password }
  bootstrap_password: { file: ./secrets/bootstrap_password }
```

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

`schema.sql` also defines two roles this image itself does **not** use, for the
companion images:

- **`mail-server-admin`** — read-write management role (INSERT/UPDATE/DELETE on
  the lookup tables). Used by [`mail-controller`](https://github.com/karol-siedlaczek/mail-controller)
  for CRUD.
- **`mail-server-chpasswd`** — password self-service role (`SELECT (email, active)`
  + `UPDATE (password)` on `users`, nothing else). Intended for a future webmail
  image (`mail-webmail`, a thin SnappyMail container) so users can change their
  own password. Create its login user only when you deploy that image:

  ```bash
  psql "$DBURL" <<'SQL'
  CREATE ROLE "mail-server-webmail_user" LOGIN PASSWORD '...';
  GRANT "mail-server-chpasswd" TO "mail-server-webmail_user";
  SQL
  ```

> Use `--` for SQL comments inside these blocks, **not** `#` — psql treats `#`
> as a syntax error, and every statement must end with a semicolon.

`schema.sql` is **idempotent** — re-run it on an existing database to pick up
additions. In particular it installs a `NOTIFY forwardings_changed` trigger that
lets the Sieve forwarding sync react to `forwardings` changes instantly. The
trigger is **optional**: without it the sync still refreshes on a ~60s fallback
timer, so re-applying `schema.sql` only upgrades update latency from ~60s to ~1s.
It needs no new grants (`pg_notify` runs as the writer; the daemon only `LISTEN`s).

## DNS & delivery prerequisites

Everything below is set up **outside the container** — in your DNS zone, at your
IP provider, and on your reverse proxy — and is required for mail to be accepted
and delivered. Start with the DNS records:

For every domain you host (substitute `example.com`, your MX host, the DKIM TXT
printed by `mail-dkim-keygen`):

| Type | Name | Value |
|------|------|-------|
| A | `mail.example.com` | the server's static IPv4 (and `AAAA` for IPv6) |
| MX | `example.com` | `10 mail.example.com.` |
| SPF (TXT) | `example.com` | `v=spf1 mx -all` |
| DKIM (TXT) | `default._domainkey.example.com` | `v=DKIM1; k=rsa; p=<key>` (from `mail-dkim-keygen`) |
| DMARC (TXT) | `_dmarc.example.com` | `v=DMARC1; p=none; rua=mailto:dmarc-reports@example.com; fo=1; adkim=r; aspf=r; pct=100; rf=afrf; ri=86400` |
| MTA-STS (TXT) | `_mta-sts.example.com` | `v=STSv1; id=<timestamp>` |
| MTA-STS policy | `mta-sts.example.com` | HTTPS host serving `/.well-known/mta-sts.txt` (reverse proxy) |
| TLS-RPT (TXT) | `_smtp._tls.example.com` | `v=TLSRPTv1; rua=mailto:tlsrpt-reports@example.com` |

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
> **Two delivery prerequisites live outside this image — with your IP/DNS
> provider, not in any env var.** Get these wrong and correctly-signed mail still
> lands in spam or is rejected outright.
>
> - **Reverse DNS (PTR) must match `MAIL_HOSTNAME`.** A PTR record maps your
>   **IP → hostname** and is set by whoever owns the IP block (your VPS/hosting
>   provider), not in your own DNS zone. Receivers (Gmail, Outlook…) require all
>   three to agree — forward (A: `MAIL_HOSTNAME → IP`), HELO (this image announces
>   `MAIL_HOSTNAME`), and reverse (PTR: `IP → MAIL_HOSTNAME`). A mismatch is the
>   single most common cause of "my server runs but Gmail bounces everything".
> - **Use a static IP that is not on the Spamhaus PBL.** The Policy Block List
>   covers residential/dynamic ISP ranges, which "should not send mail directly".
>   Even with perfect config, sending from such an IP is rejected — you need a
>   **static-IP VPS / dedicated server** whose provider also lets you set the PTR
>   (confirm this *before* you buy; cheap hosts often don't).
> - **DANE/TLSA only with DNSSEC.** DANE publishes your TLS certificate's
>   fingerprint in DNS. It is only trustworthy if the zone is **DNSSEC-signed**
>   (otherwise the record can be spoofed). No DNSSEC → skip TLSA and rely on
>   MTA-STS (above) instead.

## Operations

- **Health:** `docker compose exec mail-server /usr/local/bin/healthcheck.sh` —
  aggregates `postfix status`, `doveadm service status`, `rspamadm control stat`,
  and (if configured) Redis `PING`. `start-period` ~120s for warm-up.
- **Queue:** `docker compose exec mail-server postqueue -p` (list) / `postqueue -f`
  (flush). Shutdown raises `S6_KILL_GRACETIME` (~20s) so the queue drains.
- **Config sanity:** `postfix check`, `doveconf -n`, `rspamadm configtest`.
- **Logs / audit:** daemon logs on container stdout; durable audit in the
  `audit_logs` table (query by `login`, `timestamp`, `queue_id`).
- **Add a domain/user later:** insert rows in Postgres (the future `mail-controller`,
  or SQL); lookups need no restart. Generate and publish its DKIM key per
  [DKIM keys](#dkim-keys).
- **Back up three things independently:** `/var/vmail`, the DKIM/ARC keys under
  `/var/lib/rspamd/dkim`, and the Postgres DB (`pg_dump`). Test restores.
- **Brute-force protection (fail2ban):** this image does **not** ban abusive IPs
  itself — it logs auth failures to stdout and leaves banning to a **separate
  fail2ban container** on the host that watches `docker logs mail-server` and
  drops offenders at the firewall. See
  [`docs/fail2ban-setup-prompt.md`](docs/fail2ban-setup-prompt.md) for a ready-to-use
  setup.

### DKIM keys

Private signing keys live in the persistent Rspamd volume, one PEM per
domain/selector:

```bash
# Selector defaults to 'default'
/var/lib/rspamd/dkim/<domain>.<selector>.key      
```

List what exists:

```bash
docker compose exec mail-server ls -l /var/lib/rspamd/dkim/
```

If you booted **without** `MAIL_BOOTSTRAP_DOMAIN` (so the Day 1 seed was skipped),
this directory is empty and no signing key was generated — create one by hand:

```bash
docker compose exec mail-server mail-dkim-keygen example.com default
```

This writes `example.com.default.key` and prints the DNS TXT to publish at
`default._domainkey.example.com`. Then point the domain at the selector and
reload Rspamd so it signs with the new key:

1. set `domains.dkim_selector = 'default'` for the domain in Postgres (or via
   `mail-controller`),
2. `docker compose exec mail-server s6-svc -r /run/service/rspamd` (or restart the
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
  mail-server:
    environment:
      LOCAL_RESOLVER_ENABLED: "false"
    dns:
      - 10.0.0.53   # your resolver — must RECURSE, not forward to 8.8.8.8/1.1.1.1,
                    # or Spamhaus sees the public resolver and blocks the query
```

Docker keeps `127.0.0.11` in the container (service names resolve) and forwards
external queries to your resolver. A forwarder that relays to a public resolver
defeats the purpose — it must do its own recursion.

### Reading a mailbox

Mail is stored as Maildir under `/var/vmail/<domain>/<localpart>/Maildir`.
Three ways to read it:

**IMAP client** (Thunderbird, Outlook, mobile) — the normal way:
- Server `mail.example.com`, port **993** (implicit TLS) or **143** (STARTTLS)
- Username = the **full address** (e.g. `alice@example.com`), password = the user's password
- Submission for sending: **587** (STARTTLS) / **465** (implicit TLS), same credentials

**Server-side peek with `doveadm`** (no client needed — handy for debugging):
```bash
docker compose exec mail-server doveadm mailbox list -u alice@example.com
docker compose exec mail-server doveadm fetch -u alice@example.com \
  "date.received hdr.from hdr.subject" mailbox INBOX all
docker compose exec mail-server doveadm fetch -u alice@example.com "text" mailbox Junk all
```

**On disk** (raw Maildir):
```bash
docker compose exec mail-server ls -l /var/vmail/example.com/alice/Maildir/{new,cur}
```

The mailbox directory is created on the user's first login or first local
delivery. Note (with Sieve forwarding enabled): a message forwarded with
`keep_copy=false` leaves no local copy, but mail marked spam is **never
forwarded** — it is filed into the local `Junk` mailbox instead (see
[Spam filtering & training](#spam-filtering--training)), so a forward-only
mailbox still accumulates a reviewable spam queue there.

### Spam filtering & training

Rspamd scores every inbound message; at score ≥ `add_header` (6) it stamps
`X-Spam: Yes`. The Sieve forward script keys on that header: clean mail is
redirected to the external destination, **spam is filed to the recipient's
`Junk` mailbox and never relayed** (this protects the server's sending
reputation — forwarding spam gets the whole IP rate-limited by Gmail et al.).

Two learning systems raise detection of mail that stock rules miss (a
technically compliant bulk sender can score below 6 and slip through):

- **Bayes** (`classifier-bayes.conf`) — statistical, learns vocabulary. Autolearn
  is conservative (`[-2, 12]`: ham below −2, spam above 12); the murky middle
  must be taught by hand. It emits no verdict until ~50 messages of each class
  are learned, so it is a slow-burn generalizer, not a day-one fix.
- **Fuzzy** (`fuzzy_check.conf`) — content fingerprints. The public `rspamd.com`
  feed (stock, read-only) catches global campaigns immediately; a private local
  store catches re-sends of campaigns you teach it. Fuzzy is the immediate win —
  one `mail-learn-spam` and near-duplicate re-sends are caught at once.

**Review the Junk queue** over IMAP, or server-side:

```bash
docker compose exec mail-server doveadm fetch -u karol@example.com \
  "date.received hdr.from hdr.subject" mailbox Junk all
```

**Training helpers** (bundled in the image). Feed them a raw `.eml` — from
Gmail use *Show original → Download original*. Over SSH the same command is
`ssh worker-01 'docker exec -i mail-server <helper>' < message.eml`.

```bash
# Teach that a message is spam (Bayes learn + local fuzzy fingerprint):
docker exec -i mail-server mail-learn-spam < message.eml

# Correct a false positive found in Junk (Bayes ham; no fuzzy on good mail):
docker exec -i mail-server mail-learn-ham < message.eml

# Release a false positive from Junk: forward it to its real destination
# (bypasses the Sieve gate, so it won't loop back to Junk) and learn it as ham:
docker exec -i mail-server mail-release karol@gmail.com < message.eml
```

`mail-learn-spam` also accepts file arguments (`mail-learn-spam /path/*.eml`) for
bulk seeding. All three authenticate to the Rspamd controller automatically when
`RSPAMD_CONTROLLER_PASSWORD` is set.

**Cheat-sheet — which action, when:**

| Situation | Do this | What it teaches |
|---|---|---|
| Spam slipped through (was forwarded) | `mail-learn-spam < msg.eml` | Bayes **spam** + local **fuzzy** fingerprint |
| Legit mail landed in `Junk`, you still want it | `mail-release <dest> < msg.eml` | forwards it out **and** Bayes **ham** |
| Legit mail in `Junk`, no need to forward | `mail-learn-ham < msg.eml` | Bayes **ham** only |
| Bulk-seed a spam folder | `mail-learn-spam /path/*.eml` | as above, per file |
| In an IMAP/webmail client: move a message **into** `Junk` | (just move it) | Bayes **spam** (no fuzzy) |
| In an IMAP/webmail client: move a message **out of** `Junk` | (just move it — not to Trash) | Bayes **ham** (no fuzzy) |

**Learn-on-move (IMAP client).** In addition to the helpers above, this image
trains **Bayes** directly from IMAP folder actions via `imap_sieve`
(`96-imapsieve.conf`):

- Moving or copying a message **into** `Junk` → Bayes **learn spam**.
- Moving a message **out of** `Junk` into any real folder → Bayes **learn ham**.

This lets anyone reviewing mail in a normal IMAP/webmail client correct the
classifier without shell access. Caveats:

- **Bayes only.** Learn-on-move does *not* touch fuzzy — no fingerprint is added
  or removed. For the immediate near-duplicate win, still use `mail-learn-spam`.
- **Emptying Junk is safe.** Moving `Junk → Trash` (i.e. deleting) is deliberately
  **excluded** from ham learning, so discarding quarantined spam never poisons the
  ham model.
- **It does not forward.** Moving a false positive out of `Junk` only re-files and
  learns it locally — it does **not** deliver it to the external destination. To
  actually release a false positive to its recipient, use `mail-release`.

Notes:
- Correcting a false positive teaches Bayes for **future** similar mail; it does
  not retroactively re-file other messages.
- `mail-release`/`mail-learn-ham` do `learn_ham` only — they do **not** remove a
  fuzzy fingerprint. If a message was previously `mail-learn-spam`'d and later
  proves legit, also run `rspamc fuzzy_del` to drop its hash.
- Bayes needs **both** classes and ~50 learns each before it emits any
  `BAYES_*` symbol, so train ham as diligently as spam (see below).

**Checking what Rspamd has learned.** `rspamc stat` prints the classifier and
action totals; grep for the interesting lines:

```bash
docker exec mail-server rspamc stat | grep -iE "learned|Messages"
```

```text
Messages scanned: 390
Messages with action reject: 49, 12.56%
Messages with action add header: 46, 11.79%
Messages with action greylist: 7, 1.79%
Messages with action no action: 288, 73.85%
Messages treated as spam: 95, 24.36%
Messages treated as ham: 295, 75.64%
Messages learned: 53
Statfile: BAYES_SPAM type: redis; ... learned: 51; users: 1; languages: 0
Statfile: BAYES_HAM  type: redis; ... learned: 2;  users: 1; languages: 0
```

How to read it:

- **`BAYES_SPAM` / `BAYES_HAM` `learned:`** — how many messages of each class
  Bayes knows. Both must reach **~50** before any `BAYES_*` symbol fires, and
  they should stay in the **same ballpark** — the example above (51 spam vs **2**
  ham) is badly lopsided and will bias toward false positives; feed it ham
  (`mail-learn-ham`, or move good mail out of `Junk`) until the counts even out.
- **`Messages learned`** — total training events (spam + ham).
- **`Messages scanned` / `treated as spam|ham` / `action …`** — live traffic
  mix, useful to sanity-check that `reject`/`add header` rates look sane for your
  volume.

> Running it over SSH from the host? Use the non-interactive form and note that
> the **first** ssh to a new host prompts to accept its key (answer `yes` once,
> or pre-seed `known_hosts`):
>
> ```bash
> ssh worker-01 'docker exec mail-server rspamc stat | grep -iE "learned|Messages"'
> ```

## Day 1: Bootstrap

> [!NOTE]
> The first-boot bootstrap needs **INSERT on `domains` and `users`**. The
> `mail-server-ro` role is SELECT-only by design. For the seed either (a) point
> `PG_USER` at a user with temporary INSERT for the first boot, or (b) skip the
> bootstrap env and insert the domain + admin rows yourself. If `mail-server-ro_user` lacks
> INSERT, `mail-bootstrap` logs the privilege error and continues the boot — it
> never wedges the container.

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
