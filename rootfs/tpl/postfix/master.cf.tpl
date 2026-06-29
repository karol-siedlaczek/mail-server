# Postfix master.cf — rendered from /tpl by render-config.
# service type  private unpriv  chroot  wakeup  maxproc command + args
# ============================================================================

# ── :25 inbound — fronted by postscreen (Phase K). When POSTSCREEN_ENABLED=false,
#    render-config rewrites this back to a plain smtpd service. NO SASL on :25.
# chroot=n: these run UNCHROOTED on purpose. The container is the isolation
# boundary; chrooting to /var/spool/postfix would require a populated jail
# (libnss, /etc/resolv.conf, /etc/services …) that nothing maintains under s6,
# so a chrooted smtpd cannot start and the :25 front-end answers 450 4.3.2.
smtp      inet  n       -       n       -       1       postscreen
smtpd     pass  -       -       n       -       -       smtpd
  -o smtpd_sasl_auth_enable=no
tlsproxy  unix  -       -       n       -       0       tlsproxy
dnsblog   unix  -       -       n       -       0       dnsblog

# ── :587 submission — STARTTLS, SASL, sender-login enforcement ──────────────
submission inet  n       -       n       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_tls_auth_only=yes
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o smtpd_sender_restrictions=reject_sender_login_mismatch,permit_sasl_authenticated,reject
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
  -o cleanup_service_name=cleanup

# ── :465 smtps — implicit TLS (wrappermode), SASL, sender-login enforcement ─
smtps      inet  n       -       n       -       -       smtpd
  -o syslog_name=postfix/smtps
  -o smtpd_tls_wrappermode=yes
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o smtpd_sender_restrictions=reject_sender_login_mismatch,permit_sasl_authenticated,reject
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
  -o cleanup_service_name=cleanup

# ── Core transports ─────────────────────────────────────────────────────────
pickup     unix  n       -       n       60      1       pickup
cleanup    unix  n       -       n       -       0       cleanup
qmgr       unix  n       -       n       300     1       qmgr
tlsmgr     unix  -       -       n       1000?   1       tlsmgr
rewrite    unix  -       -       n       -       -       trivial-rewrite
bounce     unix  -       -       n       -       0       bounce
defer      unix  -       -       n       -       0       bounce
trace      unix  -       -       n       -       0       bounce
verify     unix  -       -       n       -       1       verify
flush      unix  n       -       n       1000?   0       flush
proxymap   unix  -       -       n       -       -       proxymap
proxywrite unix  -       -       n       -       1       proxymap
smtp       unix  -       -       n       -       -       smtp
relay      unix  -       -       n       -       -       smtp
showq      unix  n       -       n       -       -       showq
error      unix  -       -       n       -       -       error
retry      unix  -       -       n       -       -       error
discard    unix  -       -       n       -       -       discard
lmtp       unix  -       -       n       -       -       lmtp
anvil      unix  -       -       n       -       1       anvil
scache     unix  -       -       n       -       1       scache
postlog    unix-dgram n  -       n       -       1       postlogd
