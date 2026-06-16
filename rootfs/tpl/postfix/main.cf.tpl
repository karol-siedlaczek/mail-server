# Postfix main.cf — rendered from /tpl by render-config (envsubst).
# Per-service overrides (SASL on 587/465, sender-login enforcement, postscreen)
# live in master.cf. SASL AUTH is offered ONLY on 587/465, NEVER on port 25.

# ── Identity ────────────────────────────────────────────────────────────────
myhostname = ${MAIL_HOSTNAME}
mydomain = ${MAIL_HOSTNAME}
myorigin = $myhostname
smtpd_banner = $myhostname ESMTP
biff = no
append_dot_mydomain = no
compatibility_level = 3.6

# Local-delivery agent is unused: all mailboxes are virtual via Dovecot LMTP.
mydestination =
local_recipient_maps =
local_transport = error:local delivery is disabled
alias_maps =
alias_database =
mynetworks_style = host

# ── Virtual mailboxes / aliases (external Postgres, see sql/postfix) ─────────
virtual_mailbox_domains = pgsql:/etc/postfix/sql/virtual_mailbox_domains.cf
virtual_mailbox_maps = pgsql:/etc/postfix/sql/virtual_mailbox_maps.cf
virtual_alias_maps = pgsql:/etc/postfix/sql/virtual_alias_maps.cf
# Final delivery of real mailboxes goes to Dovecot over LMTP (phase D).
virtual_transport = lmtp:unix:private/dovecot-lmtp
virtual_mailbox_base = /var/vmail

# Send-as ownership map (enforced per submission service in master.cf).
smtpd_sender_login_maps = pgsql:/etc/postfix/sql/sender_login_maps.cf

# ── SASL: Dovecot is the single auth authority ──────────────────────────────
smtpd_sasl_type = dovecot
smtpd_sasl_path = private/auth
# Global default OFF; only effective on services that re-enable it (587/465).
smtpd_sasl_auth_enable = no
smtpd_sasl_security_options = noanonymous
broken_sasl_auth_clients = yes

# ── Rspamd milter (both smtpd + non-smtpd so submission + local mail sign) ──
smtpd_milters = inet:localhost:11332
non_smtpd_milters = inet:localhost:11332
milter_protocol = 6
# Mail still flows if Rspamd is down; set 'tempfail' to fail closed.
milter_default_action = accept
milter_mail_macros = i {auth_type} {auth_authen} {auth_author} {mail_addr} {client_addr} {client_name} {daemon_name}

# ── Forwarding correctness ──────────────────────────────────────────────────
# 998-byte line folding would mutate the body AFTER signing and invalidate the
# ARC seal / DKIM at Gmail. Disable it on the outbound/forward path.
smtp_line_length_limit = 0

# SRS envelope rewriting via postsrsd 1.x tcp: table interface (phase H).
sender_canonical_maps = tcp:localhost:10001
sender_canonical_classes = envelope_sender
recipient_canonical_maps = tcp:localhost:10002
recipient_canonical_classes = envelope_recipient,header_recipient

# ── Relay gate (authoritative) ──────────────────────────────────────────────
# Base policy: never an open relay. Submission services widen this to
# permit_sasl_authenticated in master.cf.
smtpd_relay_restrictions = permit_mynetworks reject_unauth_destination
smtpd_recipient_restrictions = permit_mynetworks reject_unauth_destination

# ── Limits / backstops ──────────────────────────────────────────────────────
message_size_limit = ${MESSAGE_SIZE_LIMIT}
smtpd_client_connection_count_limit = 50
smtpd_client_connection_rate_limit = 60
smtpd_client_message_rate_limit = 100
anvil_rate_time_unit = 60s
relayhost = ${RELAYHOST}

# ── TLS (chain files; ports 587/465 force encrypt in master.cf) ─────────────
smtpd_tls_chain_files = ${TLS_CERT_FILE}, ${TLS_KEY_FILE}
smtpd_tls_security_level = may
smtpd_tls_protocols = >=TLSv1.2
smtpd_tls_mandatory_protocols = >=TLSv1.2
smtpd_tls_mandatory_ciphers = high
smtpd_tls_exclude_ciphers = aNULL, MD5
tls_preempt_cipherlist = yes
smtpd_tls_loglevel = 1
smtp_tls_security_level = may
smtp_tls_protocols = >=TLSv1.2

# ── postscreen on :25 (pregreet + weighted DNSBL); enabled in master.cf ─────
postscreen_greet_action = enforce
postscreen_dnsbl_threshold = 3
postscreen_dnsbl_allowlist_threshold = -1
postscreen_dnsbl_sites = zen.spamhaus.org*2 b.barracudacentral.org*1 bl.spamcop.net*1
postscreen_dnsbl_action = enforce
postscreen_pipelining_enable = yes
postscreen_pipelining_action = enforce
postscreen_non_smtp_command_enable = yes
postscreen_non_smtp_command_action = drop
postscreen_bare_newline_enable = yes
postscreen_bare_newline_action = enforce

# ── Misc ────────────────────────────────────────────────────────────────────
maillog_file = /dev/stdout
queue_directory = /var/spool/postfix
disable_vrfy_command = yes
smtputf8_enable = no
