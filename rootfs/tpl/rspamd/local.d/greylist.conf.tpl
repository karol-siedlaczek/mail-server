# Rspamd greylisting (Phase K). Soft-rejects (451) the first time an unknown
# triplet (from/to/ip-subnet) is seen, accepting on retry — cheap, effective
# against spambots that don't retry. The shared Redis backend is configured in
# redis.conf; we only set the namespaced key prefix here.
enabled = true;
# NEVER greylist authenticated submission — our own users must send instantly.
check_authed = false;
# Local/own-network senders are exempt too.
check_local = false;
key_prefix = "${REDIS_PREFIX}_gr";
# Greylist window: re-offer after 5 min, remember the triplet for 9h, expire
# whitelisted triplets after 36h.
timeout = 300s;
expire = 32400s;
expire_white = 129600s;
# Only greylist mail that is already leaning spammy (Rspamd 'greylist' action
# threshold from actions.conf) — clean mail is never delayed.
greylist_min_score = 4;
