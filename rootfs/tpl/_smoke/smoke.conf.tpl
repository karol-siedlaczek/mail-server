# Smoke template — proves render-config's envsubst loop runs end-to-end.
# Rendered to /run/mail-render-smoke.conf. Not consumed by any daemon.
hostname = ${MAIL_HOSTNAME}
pg = ${PG_USER}@${PG_HOST}:${PG_PORT}/${PG_DBNAME}
redis = ${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB} prefix=${REDIS_PREFIX}
reject_score = ${RSPAMD_REJECT_SCORE}
