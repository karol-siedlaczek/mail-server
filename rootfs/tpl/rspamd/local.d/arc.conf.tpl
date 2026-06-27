# ARC sealing.  sign_inbound = true seals mail we forward so the original
# SPF/DKIM/DMARC result is carried to the next hop (e.g. Gmail) even though
# forwarding breaks plain DKIM alignment.  Uses the same per-domain key maps
# as dkim_signing.
enabled = true;
sign_inbound = true;
sign_authenticated = true;
sign_local = true;
use_domain = "header";
try_fallback = false;

selector_map = "/etc/rspamd/dkim/selectors.map";
path_map = "/etc/rspamd/dkim/paths.map";
