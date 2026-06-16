# postfix-pgsql map: virtual_mailbox_maps
# Rendered from env by render-config (phase A). Role: mail_ro.
# %s = the full recipient address Postfix is testing.
hosts = ${PG_HOST}:${PG_PORT}
dbname = ${PG_DBNAME}
user = ${PG_USER}
password = ${PG_PASSWORD}
query = SELECT 1 FROM users WHERE email=lower('%s') AND active
