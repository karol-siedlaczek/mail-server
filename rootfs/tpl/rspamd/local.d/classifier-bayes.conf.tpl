# Bayesian statistical classifier (merged into the stock `classifier "bayes"`).
# The Redis backend servers + key prefix (`${REDIS_PREFIX}_bayes`) are configured
# globally in redis.conf; here we only tune learning.
#
# Role: Bayes is the slow-burn generalizer behind the ham-forwarding spam gate.
# Once trained it raises the score of look-alike mail, pushing "clean" spam over
# add_header=6 so it gets X-Spam: Yes and is filed to Junk instead of forwarded.
# It emits no symbols until ~min_learns samples per class exist, so it is useless
# on day one — seed it with the mail-learn-spam / mail-learn-ham helpers.
backend = "redis";

# Conservative autolearn: learn ham when the final score is < -2, spam when > 12.
# The neutral band in between — where hard-to-catch compliant-sender bulk spam
# lands (the sample that triggered this work scored -0.7) — is NEVER auto-learned.
# So autolearn only reinforces already-obvious verdicts and cannot be poisoned by
# borderline mail; that whole class must be taught by hand via the helpers.
autolearn = [-2, 12];

# Start emitting BAYES_* symbols after only 50 learns per class (stock default is
# 200) so the classifier becomes useful on a low-volume personal server sooner.
min_learns = 50;
