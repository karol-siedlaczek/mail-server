# ManageSieve — lets users upload/edit their own Sieve filters and vacation
# responder over the standard RFC 5804 port 4190 (TLS via the global ssl
# settings in 10-ssl.conf). The Sieve scripts are then executed at LMTP
# delivery time (see 15-lmtp.conf).
service managesieve-login {
  inet_listener sieve {
    port = 4190
  }
}

protocol sieve {
  # Sensible ceilings so a user can't upload an unbounded script set.
  managesieve_max_line_length = 65536
}
