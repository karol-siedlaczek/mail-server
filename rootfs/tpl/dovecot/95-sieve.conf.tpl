# Global Sieve run BEFORE any personal (ManageSieve) script, at LMTP delivery.
# The forward script is generated from psql `forwardings` by sieve-forward-sync
# and does the spam-gated external redirect. Personal scripts (~/.dovecot.sieve
# via ManageSieve) still run afterwards.
#
# Dovecot 2.4 syntax: named `sieve_script` blocks. The legacy 2.3 plugin-block
# form (sieve_before inside a plugin section) was removed in 2.4
# (see doc.dovecot.org/2.4.x sieve settings).

# One alias can fan out to several destinations (e.g. a shared mailbox to 5
# people). Pigeonhole's default cap is sieve_max_redirects = 4, checked at
# COMPILE time against the whole script — exceed it and the ENTIRE generated
# forward script fails to load, silently disabling all forwarding. Configurable
# via SIEVE_MAX_REDIRECTS (default 25).
sieve_max_redirects = ${SIEVE_MAX_REDIRECTS}

sieve_script personal {
  path = ~/sieve
  active_path = ~/.dovecot.sieve
}

sieve_script forward {
  type = before
  path = /var/lib/dovecot/sieve/forward.sieve
}
