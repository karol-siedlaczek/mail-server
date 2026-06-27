# Dovecot 2.4 SQL auth backend — Postgres lookups for passwords (passdb) and
# mailbox location/quota (userdb). All rows live in the operator's external
# Postgres; this file only carries the connection + the operator-editable
# queries. 2.4 syntax differs sharply from 2.3: a NAMED `pgsql <name> {}` block
# with `parameters {}` replaces the old 2.3 connection string, and `passdb sql {}`
# carries a single inline `query =` (no driver/args lines). User variables use
# the `%{user}` form, not `%u`.
sql_driver = pgsql

pgsql maildb {
  parameters {
    host = ${PG_HOST}
    port = ${PG_PORT}
    dbname = ${PG_DBNAME}
    user = ${PG_USER}
    password = ${PG_PASSWORD}
  }
}

# Default scheme for stored hashes that carry no explicit {SCHEME} prefix.
# New passwords are ARGON2ID; per-row {MD5-CRYPT} prefixes still override this.
passdb_default_password_scheme = ${DOVECOT_PASSWORD_SCHEME}

# Weak (legacy) schemes such as MD5-CRYPT verify ONLY when this is `yes`.
# Default `no`; render-config sets `yes` solely while ALLOW_WEAK_SCHEMES=true
# during a one-off {MD5-CRYPT} migration, then it must be turned back off.
auth_allow_weak_schemes = ${DOVECOT_AUTH_ALLOW_WEAK}

# Password lookup. Returns the scheme-prefixed hash for an active mailbox.
passdb sql {
  query = SELECT password FROM users WHERE email = '%{user}' AND active
}

# Mailbox lookup. Fixed vmail uid/gid 5000 (created in the image); home is
# derived from the address; quota_storage_size is NULL (=unlimited) when
# quota_bytes is 0 so Dovecot imposes no limit.
userdb sql {
  query = SELECT 5000 AS uid, 5000 AS gid, \
                 '/var/vmail/' || split_part(email,'@',2) || '/' || split_part(email,'@',1) AS home, \
                 CASE WHEN quota_bytes > 0 THEN quota_bytes END AS quota_storage_size \
          FROM users WHERE email = '%{user}' AND active
}
