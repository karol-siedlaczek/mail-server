auth_policy_server_url = http://127.0.0.1:4001/
auth_policy_hash_nonce = ${AUDIT_POLICY_NONCE}
auth_policy_report_after_auth = yes
auth_policy_check_before_auth = no
auth_policy_check_after_auth = no
auth_policy_hash_truncate = 12
# Dovecot 2.4's default auth_policy_request_attributes already reports the
# fields audit-svc needs (login, remote IP, service/protocol, success), and the
# 2.4 syntax for overriding it is a named block, not a flat string-list
# assignment (that form fails to parse: "Setting is a string list, use
# 'auth_policy_request_attributes {'"). We keep the defaults rather than
# restate them, so no override is rendered here.
