-- Test seed data, applied AFTER sql/schema.sql (created in phase B).
-- Loaded by tests/compose.test.yml into postgres:16 via /docker-entrypoint-initdb.d.
-- Idempotent: safe to re-run against an already-seeded DB.
--
-- Password hashes are placeholder ARGON2ID-format strings (NOT real). The auth
-- phase regenerates them with `doveadm pw -s ARGON2ID` against the built image.
-- The plaintext both placeholders stand in for is documented as 'test1234'.

-- Domain: example.test, DKIM selector 'test'.
INSERT INTO domains (domain, dkim_selector, active)
VALUES ('example.test', 'test', true)
ON CONFLICT (domain) DO NOTHING;

-- Users alice@ and bob@example.test (active mailboxes, default unlimited quota).
INSERT INTO users (email, domain_id, password, quota_bytes, active)
SELECT 'alice@example.test', d.id,
       '{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=1$YWxpY2VzYWx0YWFh$0000000000000000000000000000000000000000000',
       0, true
FROM domains d WHERE d.domain = 'example.test'
ON CONFLICT (email) DO NOTHING;

INSERT INTO users (email, domain_id, password, quota_bytes, active)
SELECT 'bob@example.test', d.id,
       '{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=1$Ym9ic2FsdGJiYg$1111111111111111111111111111111111111111111',
       0, true
FROM domains d WHERE d.domain = 'example.test'
ON CONFLICT (email) DO NOTHING;

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
