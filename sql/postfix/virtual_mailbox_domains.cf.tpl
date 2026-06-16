# postfix-pgsql map: virtual_mailbox_domains
# Rendered from env by render-config (phase A). Role: mail_ro.
# %s = the recipient domain Postfix is testing.
hosts = ${PG_HOST}:${PG_PORT}
dbname = ${PG_DBNAME}
user = ${PG_USER}
password = ${PG_PASSWORD}
query = SELECT 1 FROM domains WHERE domain=lower('%s') AND active
