#!/bin/sh
# Called by Pigeonhole imap_sieve (sieve_extprograms) with the message on stdin
# when a user moves/copies mail INTO Junk. Learns it as SPAM in Bayes. The rspamd
# controller trusts loopback (secure_ip), so no password is needed here.
# Bound the wait (-t) so a stuck controller can't stall the IMAP client, and
# swallow the expected non-zero exit (e.g. "already learned") so imap_sieve does
# not log a pipe failure on every action.
/usr/bin/rspamc -t 10 learn_spam >/dev/null 2>&1 || true
