# Known-good fixture environment for render-config tests.
# Source this, then export the vars you want. Secret files are created by the
# test in a tmpdir and pointed at via *__FILE.
MAIL_HOSTNAME=mail.example.test
PG_HOST=postgres
PG_DBNAME=maildb
PG_USER=mail-server-ro_user
REDIS_HOST=redis
