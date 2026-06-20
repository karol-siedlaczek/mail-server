# Rspamd ratelimit (Phase K). One outbound bucket keyed on the authenticated
# SASL user, so a single compromised account can send at most 200 messages/hour
# before being rate-limited — containment, not a hard mail policy. Inbound and
# unauthenticated paths are not limited here (postscreen/anvil cover those).
# Redis backend is from redis.conf; only the namespaced prefix is set here.
key_prefix = "${REDIS_PREFIX}_rl";
# Bounce (soft-reject with a clear message) rather than silently drop, so a
# legitimate burst sender immediately knows they were limited.
bounce_to = true;
# Only count authenticated submission toward the bucket.
rates {
  user = {
    selector = 'user';
    bucket = {
      burst = 200;
      rate = "200 / 1h";
    }
  }
}
# Don't rate-limit our own infrastructure.
whitelisted_rcpts = "postmaster,mailer-daemon";
