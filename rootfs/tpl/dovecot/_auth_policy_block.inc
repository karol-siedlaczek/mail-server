auth_policy_server_url = http://127.0.0.1:4001/
auth_policy_hash_nonce = ${AUDIT_POLICY_NONCE}
auth_policy_report_after_auth = yes
auth_policy_check_before_auth = no
auth_policy_check_after_auth = no
auth_policy_hash_truncate = 12
auth_policy_request_attributes = login=%{requested_username} pwhash=%{hashed_password} remote=%{rip} protocol=%{protocol} success=%{success}
