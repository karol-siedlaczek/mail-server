# LMTP delivery service. Postfix hands inbound mail (recipients that matched
# virtual_mailbox_maps) to Dovecot over this socket; the socket lives inside the
# Postfix queue dir so chrooted smtpd/lmtp can reach it at `private/dovecot-lmtp`
# (matching `virtual_transport = lmtp:unix:private/dovecot-lmtp` in main.cf).
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}

# Run the Sieve interpreter during LMTP delivery (filing into folders, vacation,
# fileinto/redirect). User scripts are managed over ManageSieve (see 20-).
protocol lmtp {
  mail_plugins {
    sieve = yes
  }

  # Preserve the full email address (user@domain) for LMTP delivery lookups.
  # The Debian default 20-lmtp.conf sets auth_username_format to strip the
  # domain (for /etc/passwd compatibility). Since we use virtual mailboxes
  # keyed by full email in Postgres, we must override it back to the bare
  # %{user} (no transformation) so the SQL query finds alice@example.test.
  # This file renders to 20-lmtp.conf and completely replaces the Debian default.
  auth_username_format = %{user}
}
