# Guarantee a deterministic spam marker for the Sieve forward gate. Rspamd adds
# "X-Spam: Yes" when the action is 'add header' (score >= the add_header action
# in actions.conf). The sieve-forward-sync script forwards a message ONLY when
# this header is absent, so spam is never relayed to external mailboxes.
use = ["x-spam-header", "x-spamd-result", "authentication-results"];
spam_header = "X-Spam";
spam_header_value = "Yes";
authenticated_headers = ["authentication-results"];
