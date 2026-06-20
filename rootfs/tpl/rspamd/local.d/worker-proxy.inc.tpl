# Rspamd milter proxy worker — entry point for Postfix's smtpd_milters /
# non_smtpd_milters (inet:localhost:11332).  Rendered from /tpl by render-config.
#
# The proxy worker forwards to the local normal worker (localhost:11333).
# Note: the legacy single-worker mode (rspamd < 4.x) is not used here.
milter = yes;
timeout = 120s;

bind_socket = "*:11332";

# Forward to the normal worker running on the same host.
upstream "local" {
  default = yes;
  hosts = "localhost:11333";
}

# Trust XCLIENT/own loopback so the real client IP from Postfix is honoured.
count = 1;
max_retries = 5;
discard_on_reject = false;
quarantine_on_reject = false;
