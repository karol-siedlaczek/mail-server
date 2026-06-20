-- Auth event: one row per Dovecot auth-policy report (success AND failure).
-- Bound via psycopg2 named params from audit-svc.parse_auth_report().
INSERT INTO audit_logs (event_type, success, login, src_ip, host, msg, "timestamp")
VALUES ('auth', %(success)s, %(login)s, %(src_ip)s, %(host)s, %(msg)s, now());
