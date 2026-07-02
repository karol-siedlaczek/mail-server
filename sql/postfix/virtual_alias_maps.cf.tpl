# postfix-pgsql map: virtual_alias_maps
# Rendered from env by render-config (phase A). Role: mail-server-ro.
# Applied to EVERY recipient before mailbox routing. Plain aliases (sources with no
# mailbox) are unconditionally redirected. Mailboxed sources are NOT redirected here
# (delivered to Dovecot/LMTP instead, where Sieve handles spam-gated forwards).
# %s = the recipient address being rewritten.
hosts = ${PG_HOST}:${PG_PORT}
dbname = ${PG_DBNAME}
user = ${PG_USER}
password = ${PG_PASSWORD}
# Forwarding is applied by Postfix ONLY for sources that are NOT local mailbox
# users. For local mailboxes the address is delivered to Dovecot (LMTP) and the
# sieve-forward-sync Sieve script does a spam-gated redirect instead, so we must
# NOT redirect them here (that would bypass Dovecot/rspamd filtering). Plain
# aliases (source with no mailbox) keep the classic unconditional redirect.
query = SELECT f.destination FROM forwardings f
         WHERE f.source = lower('%s') AND f.active
           AND NOT EXISTS (SELECT 1 FROM users u
                            WHERE u.email = lower('%s') AND u.active)
