# Learn-on-move: train rspamd Bayes from IMAP folder actions. Moving/copying a
# message INTO Junk teaches spam; moving it OUT of Junk teaches ham. This is the
# IMAP-time counterpart to the delivery-time forward script (95-sieve.conf) and
# is independent of it. Bayes only — fuzzy stays manual (mail-learn-spam).
#
# Dovecot 2.4.1 / Pigeonhole syntax (verified with `doveconf -n`): imap_sieve is a
# per-protocol plugin; per-mailbox rules use `mailbox <name> { sieve_script … {
# type = before; cause = copy; path = … } }`, and the "moved out of" direction
# uses an `imapsieve_from <name> { … }` block. The 2.3 numbered
# `imapsieve_mailbox1_*` settings were removed in 2.4 and are rejected by doveconf.
protocol imap {
  mail_plugins {
    imap_sieve = yes
  }
}

sieve_plugins = sieve_imapsieve sieve_extprograms
sieve_pipe_bin_dir = /usr/lib/dovecot/sieve
sieve_global_extensions {
  vnd.dovecot.pipe = yes
}

# Copy/move/append INTO Junk → report-spam.sieve → rspamc learn_spam.
# `append` also covers clients that upload a message straight into Junk (some
# "report spam" flows / imports), not only IMAP moves (which are a copy).
mailbox Junk {
  sieve_script report-spam {
    type = before
    cause = copy append
    path = /etc/dovecot/sieve/report-spam.sieve
  }
}

# Move OUT of Junk into any other mailbox → report-ham.sieve → rspamc learn_ham
imapsieve_from Junk {
  sieve_script report-ham {
    type = before
    cause = copy
    path = /etc/dovecot/sieve/report-ham.sieve
  }
}
