# Authentication. Dovecot is the SINGLE SASL authority for the whole image:
# IMAP clients AND Postfix submission/smtps all authenticate here (Postfix
# reaches it through the private/auth unix socket below). No saslauthd/PAM/Cyrus.

# PLAIN + LOGIN only — the mechanisms SMTP submission and IMAP clients use
# over TLS. (No CRAM/DIGEST: they require reversible secrets we don't store.)
auth_mechanisms = plain login

# Enabled protocols. An extra protocol is appended when the optional mail
# retrieval protocol is enabled (render-config expands DOVECOT_POP3_PROTOCOLS).
protocols = imap lmtp sieve${DOVECOT_POP3_PROTOCOLS}

# SQL passdb/userdb (Postgres). Defined in its own file for clarity.
!include auth-sql.conf

service auth {
  # SASL socket for Postfix. It lives INSIDE the Postfix queue dir so smtpd
  # (chrooted to /var/spool/postfix) can reach it at the relative path
  # `private/auth`, matching `smtpd_sasl_path = private/auth` in main.cf.
  # mode 0660 + owner postfix lets only postfix read it.
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }

  # ARGON2ID hashing is memory-hard; the auth process needs a much larger
  # address-space limit than the default or verification is OOM-killed.
  vsz_limit = 2G
}

# Optional protocol service listeners. Empty when disabled.
${DOVECOT_POP3_SERVICES}
