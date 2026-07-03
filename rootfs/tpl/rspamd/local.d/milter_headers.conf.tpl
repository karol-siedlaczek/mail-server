# Guarantee a deterministic spam marker for the Sieve forward gate. Rspamd's
# milter_headers 'spam-header' routine adds a header on the add-header action
# (score >= the add_header action in actions.conf); we customise it to
# "X-Spam: Yes". The sieve-forward-sync script forwards a message ONLY when this
# header is absent, so spam is never relayed to external mailboxes.
use = ["spam-header", "x-spamd-result", "authentication-results"];

routines {
  "spam-header" {
    header = "X-Spam";
    value = "Yes";
  }
}
