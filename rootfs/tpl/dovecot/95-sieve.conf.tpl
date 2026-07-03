# Global Sieve run BEFORE any personal (ManageSieve) script, at LMTP delivery.
# The forward script is generated from psql `forwardings` by sieve-forward-sync
# and does the spam-gated external redirect. Personal scripts (~/.dovecot.sieve
# via ManageSieve) still run afterwards.
plugin {
  sieve_before = /var/lib/dovecot/sieve/forward.sieve
  sieve = file:~/sieve;active=~/.dovecot.sieve
}
