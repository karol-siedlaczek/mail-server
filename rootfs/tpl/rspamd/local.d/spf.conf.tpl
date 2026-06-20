# SPF verification.  On by default in Rspamd; declared explicitly so the
# appliance's stance is visible and the module stays enabled.  Results feed
# DMARC alignment and the Authentication-Results header.
disabled = false;
# Honour at most this many DNS lookups per SPF check (RFC 7208 hard limit).
max_dns_nesting = 10;
