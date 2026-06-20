# DKIM signing for outbound mail.  Rspamd has no SQL key backend, so the key
# path and selector for each signing domain come from maps rendered at boot by
# render-config from `SELECT domain, dkim_selector FROM domains WHERE active`.
#
# use_domain = header -> sign with the From: header domain (DMARC-aligned).
# try_fallback = false -> only sign domains present in the maps; never invent a
# key path for an unknown domain.
enabled = true;
sign_authenticated = true;
sign_local = true;
use_domain = "header";
allow_hdrfrom_mismatch = false;
allow_username_mismatch = true;
try_fallback = false;

# domain -> selector  and  domain -> private key path.
selector_map = "/etc/rspamd/dkim/selectors.map";
path_map = "/etc/rspamd/dkim/paths.map";
