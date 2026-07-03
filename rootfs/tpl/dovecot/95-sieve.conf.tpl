# Global Sieve run BEFORE any personal (ManageSieve) script, at LMTP delivery.
# The forward script is generated from psql `forwardings` by sieve-forward-sync
# and does the spam-gated external redirect. Personal scripts (~/.dovecot.sieve
# via ManageSieve) still run afterwards.
#
# Dovecot 2.4 syntax: named `sieve_script` blocks. The legacy 2.3 plugin-block
# form (sieve_before inside a plugin section) was removed in 2.4
# (see doc.dovecot.org/2.4.x sieve settings).
sieve_script personal {
  path = ~/sieve
  active_path = ~/.dovecot.sieve
}

sieve_script forward {
  type = before
  path = /var/lib/dovecot/sieve/forward.sieve
}
