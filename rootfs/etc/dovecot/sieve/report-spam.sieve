# imap_sieve: runs when a message is copied/appended INTO Junk. Pipes the message
# to the learn-spam wrapper (sieve_pipe_bin_dir). Bayes only — no fuzzy.
require ["vnd.dovecot.pipe"];
pipe "rspamd-learn-spam.sh";
