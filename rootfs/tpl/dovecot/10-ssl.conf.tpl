# TLS for IMAP/ManageSieve. Certs are mounted read-only (Let's Encrypt);
# TLS_CERT_FILE/TLS_KEY_FILE default to /tls/fullchain.pem and /tls/privkey.pem.
# When TLS_CHAIN_FILE is set, both directives point at that one combined PEM
# (Dovecot reads the cert and key objects from it independently). When the
# file(s) are absent, render-config writes a self-signed pair so the container
# still starts (test/dev). 2.4 renamed the SSL directives to the ssl_server_*
# family.
ssl = yes
ssl_server_cert_file = ${DOVECOT_SSL_CERT_FILE}
ssl_server_key_file = ${DOVECOT_SSL_KEY_FILE}

# Hardening: TLS 1.2 floor (1.0/1.1 are dead), and we choose the cipher order.
ssl_min_protocol = TLSv1.2
# ssl_prefer_server_ciphers = yes  # removed in Dovecot 2.4; server always prefers
