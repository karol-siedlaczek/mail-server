# Rspamd milter proxy worker — entry point for Postfix's smtpd_milters /
# non_smtpd_milters (inet:localhost:11332).  Rendered from /tpl by render-config.
#
# self_scan mode: this proxy worker performs the scan itself instead of
# forwarding to a separate "normal" worker, which keeps the appliance to a
# single Rspamd process.
milter = yes;
timeout = 120s;

bind_socket = "*:11332";

# Scan inline in the proxy (self_scan = yes; no separate normal worker).
self_scan = yes;

# Trust XCLIENT/own loopback so the real client IP from Postfix is honoured.
count = 1;
max_retries = 5;
discard_on_reject = false;
quarantine_on_reject = false;
