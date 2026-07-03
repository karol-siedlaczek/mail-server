# Guarantee a deterministic spam marker for the Sieve forward gate. Rspamd's
# milter_headers 'spam-header' routine adds this header for ANY action above
# greylist (add header, rewrite subject, soft reject, reject, quarantine) — i.e.
# any spammy verdict, not only "add header"; we customise it to "X-Spam: Yes".
# The sieve-forward-sync script forwards a message ONLY when this header is
# absent, so spam is never relayed to external mailboxes. Note the boundary:
# mail scoring in the greylist band (below add_header) gets NO X-Spam header and
# IS forwarded — the gate fails safe (it over-suppresses, never over-forwards).
use = ["spam-header", "x-spamd-result", "authentication-results"];

routines {
  "spam-header" {
    header = "X-Spam";
    value = "Yes";
  }
}
