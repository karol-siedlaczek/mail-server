-- Test seed data, applied AFTER sql/schema.sql (created in phase B).
-- Loaded by tests/compose.test.yml into postgres:16 via /docker-entrypoint-initdb.d.
-- Idempotent: safe to re-run against an already-seeded DB.
--
-- Password hashes are real ARGON2ID hashes for plaintext 'secret' (contract).
-- Generated via: doveadm pw -s ARGON2ID -p secret

-- Domain: example.test, DKIM selector 'test'.
INSERT INTO domains (domain, dkim_selector, active)
VALUES ('example.test', 'test', true)
ON CONFLICT (domain) DO NOTHING;

-- Users alice@ and bob@example.test (active mailboxes, default unlimited quota).
-- Password: 'secret' (ARGON2ID hash).
INSERT INTO users (email, domain_id, password, quota_bytes, active)
SELECT 'alice@example.test', d.id,
       '{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=1$tUxNSOgf0jT1oMzzoF6rcg$p6XMJJjsnJfQs7CTjDCzkXhJLvwfEdq1RikWbVU3mpI',
       0, true
FROM domains d WHERE d.domain = 'example.test'
ON CONFLICT (email) DO UPDATE SET password = EXCLUDED.password;

INSERT INTO users (email, domain_id, password, quota_bytes, active)
SELECT 'bob@example.test', d.id,
       '{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=1$cKMgQr7WOceIYU5pxOL6cQ$TYpEfhPI8T1zXrBrBOZWiV+kW5JJh+RIN4ShLZE566k',
       0, true
FROM domains d WHERE d.domain = 'example.test'
ON CONFLICT (email) DO UPDATE SET password = EXCLUDED.password;

-- Forwarding fwd@example.test -> an external mailbox (redirect, no local copy).
INSERT INTO forwardings (source, destination, keep_copy, active)
VALUES ('fwd@example.test', 'external@sink.test', false, true)
ON CONFLICT (source, destination) DO NOTHING;

-- keep_copy forwarding: alice's mail is forwarded externally AND kept locally.
INSERT INTO forwardings (source, destination, keep_copy, active)
VALUES ('alice@example.test', 'external@sink.test', true, true)
ON CONFLICT (source, destination) DO NOTHING;

-- Send-as grant: bob@ may set envelope MAIL FROM = alice@.
INSERT INTO sender_login_maps (login_email, allowed_sender, active)
VALUES ('bob@example.test', 'alice@example.test', true)
ON CONFLICT (login_email, allowed_sender) DO NOTHING;

-- audit-svc writes via mail-server-audit_user; ensure the role exists with INSERT in tests.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mail-server-audit_user') THEN
    CREATE ROLE "mail-server-audit_user" LOGIN PASSWORD 'mail_audit_test_pw';
  END IF;
END
$$;
GRANT INSERT, SELECT ON audit_logs TO "mail-server-audit_user";
GRANT USAGE, SELECT ON SEQUENCE audit_logs_id_seq TO "mail-server-audit_user";
