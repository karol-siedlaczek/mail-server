# Dovecot logging. Debian ships its own 10-logging.conf with every directive
# commented out, which leaves log_path at the `syslog` default — and no syslog
# daemon runs in this image, so every IMAP and auth line (failed logins
# included) was silently discarded. Postfix already reaches the container log
# via `maillog_file = /dev/stdout` in main.cf; this gives Dovecot the same
# treatment. The dovecot s6 run script does `fdmove -c 2 1`, so fd 2 is the
# container's stdout. Rendering this file overwrites Debian's copy.
log_path = /dev/stderr

# Log an auth result line for every attempt, carrying the client IP as `rip=`.
# The host's fail2ban `dovecot` jail matches exactly those lines — without this
# there is nothing for it to read, and IMAP brute-force goes unanswered.
auth_verbose = yes

# Keep this off outside a hands-on debugging session: it writes submitted
# passwords to the container log in plaintext.
auth_verbose_passwords = no
