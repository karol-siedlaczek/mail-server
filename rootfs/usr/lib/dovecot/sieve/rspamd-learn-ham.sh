#!/bin/sh
# Called by imap_sieve when a user moves mail OUT of Junk. Learns it as HAM.
# Bounded wait + swallow the expected non-zero exit (see rspamd-learn-spam.sh).
/usr/bin/rspamc -t 10 learn_ham >/dev/null 2>&1 || true
