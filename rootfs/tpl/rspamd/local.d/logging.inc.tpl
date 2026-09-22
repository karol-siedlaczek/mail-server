# Rspamd logging. The Debian package writes to /var/log/rspamd/rspamd.log
# inside the container, where nothing collects it — so scores, rejects and
# greylist decisions were invisible from `docker logs`. `type = console` sends
# them to stderr instead, which the rspamd s6 run script folds into the
# container log (`fdmove -c 2 1`), matching Postfix and Dovecot.
type = "console";
# There is no journal in the container; without this rspamd prefixes each line
# with systemd priority markers.
systemd = false;
