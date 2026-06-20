# Shared Redis for all Rspamd stateful modules (Bayes, fuzzy, greylist,
# ratelimit, replies, dkim signing key cache).  Rspamd has no single global
# key prefix, so each module gets a consistent ${REDIS_PREFIX}_<module> prefix
# to namespace a shared instance, plus a shared servers/db/password here.
servers = "${REDIS_HOST}:${REDIS_PORT}";
db = '${REDIS_DB}';
password = "${REDIS_PASSWORD}";
timeout = 1.0;

# Bayes statistics prefix (statistics module reads this).
key_prefix = "${REDIS_PREFIX}_bayes";

# Per-module overrides so a shared Redis stays namespaced.
greylist  { key_prefix = "${REDIS_PREFIX}_greylist"; }
ratelimit { key_prefix = "${REDIS_PREFIX}_ratelimit"; }
dkim      { key_prefix = "${REDIS_PREFIX}_dkim"; }
