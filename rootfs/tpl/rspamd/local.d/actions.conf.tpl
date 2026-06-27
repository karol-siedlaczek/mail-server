# Spam action thresholds (local.d merges into the stock metric).
# greylist/add_header are the canonical Rspamd defaults; reject is raised from
# the stock 15 to RSPAMD_REJECT_SCORE so the operator can soften it (e.g. ~20)
# while Bayes/fuzzy warm up, then tighten it.  rewrite_subject stays off.
greylist = 4;
add_header = 6;
reject = ${RSPAMD_REJECT_SCORE};
