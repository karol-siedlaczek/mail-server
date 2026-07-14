# Webmail (SnappyMail) deployment — everything for the spec

**Status:** pre-spec notes / decisions locked. Next: formal brainstorm → spec →
plan → TDD when we pick this up.
**Date:** 2026-07-13

Gives the user a modern webmail against the existing postfix/dovecot/rspamd
stack, and — crucially — a way to **see and correct false positives** that are
quarantined in the local `Junk` and never forwarded to Gmail (see
[2026-07-11-spam-false-positive-visibility](2026-07-11-spam-false-positive-visibility.md)).

## Locked decisions (2026-07-13)

- **Client:** SnappyMail (modern, light, one thin container).
- **Exposure:** subdomain `webmail.siedlaczek.com.pl`, behind **HAProxy**, own TLS
  cert. SnappyMail configured with a **predefined domain** so the user only types
  e-mail + password.
- **Packaging:** thin **custom image** (`FROM php:8.x-apache` + SnappyMail release
  tarball), NOT an upstream community image. Reason: change-password needs the
  `pdo_pgsql` PHP extension baked in, config should be reproducible (baked
  templates, not clicked in admin), and it matches the repo's `wordpress` image
  convention.
- **change-password → Postgres: IN v1** (the fiddly bit — see risk below).
- **imap_sieve learn-on-move: IN scope**, but as a **separate mail-server image
  change** (own commit/tag), so "mark as spam / move out of Junk" in SnappyMail
  (or any client) trains rspamd automatically.

## Where things live (three repos)

- **Image:** `docker-images-homelab/images/mail-webmail/` (Dockerfile, README,
  entrypoint, `conf/*.tmpl`). CI builds on tag `mail-webmail/v*`.
- **Deploy:** `docker-homelab/mail-webmail/compose.yaml` (Portainer renders env),
  + HAProxy backend for `webmail.siedlaczek.com.pl`.
- **Mail-server change (imap_sieve):** this repo (`docker/mail-server`) — dovecot
  conf + sieve scripts; its own spec/tag.

## The image (`mail-webmail`)

- `FROM php:8.3-apache` (mirror `wordpress` image style).
- PHP extensions: **`pdo_pgsql`** (change-password), plus SnappyMail's needs:
  `curl, iconv, json, dom/xml, mbstring, openssl, fileinfo, zip, gd` (most are in
  the base; `pdo_pgsql`, `zip`, `gd`, `intl` are the likely explicit adds).
- Drop a **pinned SnappyMail release** into `/var/www/html`. Persist the data dir
  (`_data_`) on a volume — it holds accounts, settings, contacts, plugin state,
  admin password.
- **Baked config via `conf/*.tmpl`** (rendered by entrypoint from env):
  - predefined domain `siedlaczek.com.pl` → IMAP `mail.siedlaczek.com.pl:993`
    (SSL), SMTP `:465` (SSL), ManageSieve `:4190`;
  - enabled plugins + their config;
  - admin panel password from env; ideally the admin path renamed / panel locked
    after first setup.
- Apache serves the app; HAProxy terminates TLS in front.

## Endpoints it talks to (already exposed by mail-server)

`993` IMAP (implicit TLS), `465` submission (implicit TLS), `4190` ManageSieve.
SnappyMail **login = IMAP auth** against dovecot — no DB needed just to log in.

## Plugins

**v1:**
- **change-password** (SQL/Postgres driver) — see risk below.
- **two-factor-authentication** (TOTP) — webmail is internet-facing.

**Later:** OpenPGP/GnuPG, Contacts + CardDAV (only with a CardDAV source),
`nextcloud` (only if Nextcloud is adopted). Skip `ldap-*` (we use SQL).

## change-password integration (the risk to de-risk in the spec)

- Target table `users(email, password, active)`; `password` is **scheme-prefixed**,
  e.g. `{ARGON2ID}$argon2id$v=19$...` (`PASSWORD_SCHEME=ARGON2ID` default).
- The driver must (a) verify the current password (SnappyMail re-auths via IMAP,
  good), then (b) `UPDATE users SET password = '{ARGON2ID}' || <hash> WHERE
  email = ? AND active`.
- **Key risk:** producing a **dovecot-compatible ARGON2ID hash** from PHP.
  PHP `password_hash($p, PASSWORD_ARGON2ID)` emits `$argon2id$v=19$m=..,t=..,p=..$..`
  which dovecot's ARGON2ID scheme should verify when stored as
  `{ARGON2ID}$argon2id$...`. **Must be tested** against dovecot. Fallbacks:
  a scheme both agree on, or a tiny endpoint/helper that shells `doveadm pw
  -s ARGON2ID` (heavier).
- **Least privilege:** give change-password a **dedicated Postgres role** with
  `UPDATE(password) ON users` only — NOT the existing `mail-server-ro` (SELECT
  only) and NOT an admin role.

## imap_sieve learn-on-move (mail-server side, separate tag)

Standard rspamd + dovecot recipe so folder actions train Bayes/fuzzy:
- dovecot: `mail_plugins += imap_sieve`; `sieve_plugins = sieve_imapsieve
  sieve_extprograms`.
- `imapsieve_mailbox1_name = Junk`, `causes = COPY APPEND` → `report-spam.sieve`
  (runs `rspamc learn_spam` via `sieve_extprograms` on the message).
- `imapsieve_mailbox2_from = Junk`, `mailbox2_name = *`, `causes = COPY` →
  `report-ham.sieve` (`rspamc learn_ham`).
- Wrapper scripts under `sieve_pipe_bin_dir` calling the existing helpers /
  `rspamc` (reuse `RSPAMD_CONTROLLER_PASSWORD`).
- This makes SnappyMail's "Spam"/move buttons train rspamd with no webmail
  plugin. Its own spec + `v*` tag on the mail-server image.

## Security / ops

- HAProxy: TLS for `webmail.siedlaczek.com.pl` (cert via the usual flow — check
  `.certhub`), HSTS.
- Lock the SnappyMail **admin panel** (`/?admin`): rename path / disable after
  setup; strong admin password from env.
- **2FA** on user logins; **fail2ban** on failed webmail logins (ecosystem
  already has fail2ban — `docs/fail2ban-setup-prompt.md`).
- Persist `_data_` volume; back it up (accounts/settings/contacts live there).
- Secrets (admin pass, change-password DB creds) via Portainer env, never in repo.

## Open items to settle at spec time

- Pin PHP + SnappyMail versions.
- Dedicated Postgres role + grant for change-password.
- HAProxy cert issuance/renewal for the new subdomain; DNS `A`/`AAAA` for
  `webmail.siedlaczek.com.pl`.
- Confirm the ARGON2ID PHP↔dovecot hash compatibility empirically.
- Decide if login is restricted to the one domain (predefined) or open.

## Related
- [[spam-fp-visibility-backlog]] / `2026-07-11-spam-false-positive-visibility.md`
- Learning + helpers: `docs/superpowers/specs/2026-07-10-bayes-fuzzy-spam-learning-design.md`
- Sieve forward gate: `docs/superpowers/plans/2026-07-01-sieve-ham-forwarding.md`
- Image repo note: `docker-images-homelab/images/mail-webmail/README.md`
