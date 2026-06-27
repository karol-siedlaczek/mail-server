# postfix-pgsql map: sender_login_maps (send-as)
# Rendered from env by render-config (phase A). Role: mail-server-ro.
# Returns every SASL login authorised to use a given envelope MAIL FROM.
# MUST be one UNION: Postfix short-circuits across multiple maps (first hit wins,
# never merged), so the implicit self-rule + explicit grants live in one query.
# Every returned value is lowercase to match the literal SASL username.
# %s = envelope MAIL FROM (full email).
hosts = ${PG_HOST}:${PG_PORT}
dbname = ${PG_DBNAME}
user = ${PG_USER}
password = ${PG_PASSWORD}
query = SELECT email       FROM users             WHERE email=lower('%s')          AND active
        UNION
        SELECT login_email  FROM sender_login_maps WHERE allowed_sender=lower('%s') AND active
