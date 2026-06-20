# Migrating a legacy MySQL mail server to Postgres

This converts the operator's old `mail.siedlaczek.org.pl` MySQL schema (Postfix +
Dovecot, `md5crypt` passwords, flat-file `virtual` aliases) into the Postgres
schema this image expects (`sql/schema.sql`). It is **not** run automatically —
copy, adjust, run by hand, verify, then point the appliance at the DB.

## Legacy -> new column mapping

| Legacy (MySQL) | New (Postgres) | Notes |
|----------------|----------------|-------|
| `domains.name` | `domains.domain` | lowercased |
| `users.email` | `users.email` | full address, lowercased; this is the SASL login |
| `users.password` (md5crypt) | `users.password` | prefixed `{MD5-CRYPT}` (see below) |
| `users.quota` (if any) | `users.quota_bytes` | bytes; `0` = unlimited |
| `forwardings.user_id` -> address | `forwardings.source` | resolve the FK to the address |
| `forwardings.destination` / `goto` | `forwardings.destination` | one row per target (split CSV) |
| flat `virtual` file aliases | `forwardings` rows | see "Flat virtual file" |

`dkim_selector` defaults to `default`; `sender_login_maps` starts empty (the
legacy server had no send-as grants). `audit_logs` starts empty.

## Step 1 — apply the schema

```bash
psql "$NEWDB" -f sql/schema.sql
```

## Step 2 — pgloader recipe

Save as `mail.load` and run `pgloader mail.load`. pgloader copies the legacy
tables into staging tables in the new DB; the post-load SQL (Step 3) reshapes them
into the real schema so a botched run never corrupts a live table.

```
LOAD DATABASE
  FROM     mysql://legacy:secret@oldhost/mailserver
  INTO     postgresql://migrate:secret@newhost/mail

WITH include drop, create tables, create indexes, reset sequences,
     workers = 4, concurrency = 1

SET work_mem to '64MB', maintenance_work_mem to '256MB'

CAST type datetime to timestamptz drop default drop not null using zero-dates-to-null,
     type date drop not null using zero-dates-to-null

INTO postgresql://migrate:secret@newhost/mail?legacy_domains
  FROM mysql://legacy:secret@oldhost/mailserver?domains

INTO postgresql://migrate:secret@newhost/mail?legacy_users
  FROM mysql://legacy:secret@oldhost/mailserver?users

INTO postgresql://migrate:secret@newhost/mail?legacy_forwardings
  FROM mysql://legacy:secret@oldhost/mailserver?forwardings

ALTER SCHEMA 'mailserver' RENAME TO 'public';
```

Adjust the connection strings, the source DB name (`mailserver`), and the table
names to your legacy schema. The three `INTO ... FROM` blocks land the legacy rows
in `legacy_domains` / `legacy_users` / `legacy_forwardings` staging tables.

## Step 3 — post-load SQL (reshape + prefix hashes)

Run against the new DB **after** pgloader. This inserts into the real tables,
**prefixing every imported md5crypt hash with `{MD5-CRYPT}`** so Dovecot verifies
it (and only while `ALLOW_WEAK_SCHEMES=true`), then maps forwardings.

```sql
BEGIN;

-- Domains: lowercase, default selector.
INSERT INTO domains (domain)
SELECT DISTINCT lower(name) FROM legacy_domains
ON CONFLICT (domain) DO NOTHING;

-- Users: lowercase email, link to the domain, prefix the legacy hash.
-- A legacy hash already starting with '{' or '$' scheme markers is left as-is;
-- a bare md5crypt ($1$...) hash gets the explicit {MD5-CRYPT} prefix.
INSERT INTO users (email, domain_id, password, quota_bytes, active)
SELECT lower(u.email),
       d.id,
       CASE
         WHEN u.password LIKE '{%}%' THEN u.password
         WHEN u.password LIKE '$1$%' THEN '{MD5-CRYPT}' || u.password
         ELSE '{MD5-CRYPT}' || u.password
       END,
       COALESCE(u.quota, 0),
       true
FROM legacy_users u
JOIN domains d ON d.domain = lower(split_part(u.email, '@', 2))
ON CONFLICT (email) DO NOTHING;

-- Forwardings: resolve the legacy user_id FK to the source address, split any
-- comma-separated destination into one row per target. keep_copy defaults false;
-- set it true afterwards for addresses that must also keep a local copy.
INSERT INTO forwardings (source, destination, keep_copy, active)
SELECT lower(su.email),
       lower(trim(dest)),
       false,
       true
FROM legacy_forwardings f
JOIN legacy_users su ON su.id = f.user_id
CROSS JOIN LATERAL unnest(string_to_array(f.destination, ',')) AS dest
WHERE trim(dest) <> '';

COMMIT;
```

If your legacy `forwardings` stores the source address directly (not a `user_id`
FK), replace the join with `lower(f.source)` and drop the `legacy_users` join.

## Step 4 — flat `virtual` file -> forwardings rows

The old server's `/etc/postfix/virtual` flat file has `source destination[,dest2]`
lines. Convert it to `forwardings` INSERTs (skip comments/blank lines), then run
the generated SQL against the new DB:

```bash
awk 'NF && $1 !~ /^#/ {
       src = $1
       for (i = 2; i <= NF; i++) {
         gsub(/,/, " ", $i)
         n = split($i, dests, " ")
         for (j = 1; j <= n; j++)
           if (dests[j] != "")
             printf "INSERT INTO forwardings (source, destination) VALUES (lower(%c%s%c), lower(%c%s%c));\n", \
                    39, src, 39, 39, dests[j], 39
       }
     }' /etc/postfix/virtual > virtual_forwardings.sql

psql "$NEWDB" -f virtual_forwardings.sql
```

(`39` is the ASCII code for a single quote, so the generated SQL is correctly
quoted.) Set `keep_copy = true` afterwards on any source that is also a real
mailbox and must keep a local copy:

```sql
UPDATE forwardings f SET keep_copy = true
WHERE EXISTS (SELECT 1 FROM users u WHERE u.email = f.source AND u.active);
```

## Step 5 — clean up and verify

```sql
DROP TABLE IF EXISTS legacy_domains, legacy_users, legacy_forwardings;
SELECT count(*) FROM domains;
SELECT count(*) FROM users;
SELECT count(*) FROM forwardings;
SELECT email, left(password, 12) AS scheme FROM users LIMIT 5;  -- expect {MD5-CRYPT}...
```

Then start the appliance with `ALLOW_WEAK_SCHEMES=true`, confirm a legacy user can
log in (IMAP/submission), and have users re-set passwords (which re-hash to
`ARGON2ID`). Once no `{MD5-CRYPT}` rows remain, set `ALLOW_WEAK_SCHEMES=false`.
