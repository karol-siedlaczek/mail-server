# Private fuzzy_storage worker (paired with the "local" rule in fuzzy_check.conf).
# rspamd's master process spawns this worker from the stock `worker "fuzzy"`
# block via its .include of local.d/worker-fuzzy.inc — no separate s6 service.
#
# Listens on loopback only: the mail-learn-spam helper writes campaign
# fingerprints here (through the controller), and inbound mail is checked against
# them by the "local" rule. Hashes are stored in the shared Redis; the servers
# and per-module prefix come from redis.conf.
bind_socket = "127.0.0.1:11335";
backend = "redis";

# Namespace our fuzzy hashes in the shared Redis instance.
key_prefix = "${REDIS_PREFIX}_fuzzy";

# Forget a hash that hasn't been seen for 90 days so stale campaigns age out.
expire = 90d;

# A single storage worker is plenty for one appliance.
count = 1;
