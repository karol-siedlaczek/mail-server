# postfix-pgsql map: virtual_alias_maps
# Rendered from env by render-config (phase A). Role: mail-server-ro.
# Applied to EVERY recipient before mailbox routing. A plain forwardings row is a
# redirect (no local copy). keep_copy=true additionally returns the source itself
# so Postfix also delivers a local copy (terminal self-mapping, no recursion).
# %s = the recipient address being rewritten.
hosts = ${PG_HOST}:${PG_PORT}
dbname = ${PG_DBNAME}
user = ${PG_USER}
password = ${PG_PASSWORD}
query = SELECT destination FROM forwardings WHERE source=lower('%s') AND active
        UNION
        SELECT lower('%s')
         WHERE EXISTS (SELECT 1 FROM forwardings f
                        WHERE f.source=lower('%s') AND f.active AND f.keep_copy)
           AND EXISTS (SELECT 1 FROM users u
                        WHERE u.email=lower('%s') AND u.active)
