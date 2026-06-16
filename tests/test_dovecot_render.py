"""Dovecot template render tests (phase F).

Tests assert that rendered Dovecot config files contain the required directives.
All tests use the render_dovecot fixture from conftest.py.
"""
import shutil
import subprocess

import pytest


# ── F.1: auth-sql.conf ────────────────────────────────────────────────────────

def test_auth_sql_named_pgsql_block(render_dovecot):
    out = render_dovecot("auth-sql.conf.tpl")
    # Dovecot 2.4: named pgsql block + sql_driver, NOT the 2.3 `connect =` line.
    assert "sql_driver = pgsql" in out
    assert "pgsql maildb {" in out
    assert "host = db" in out
    assert "dbname = mail" in out
    assert "user = mail_ro" in out
    assert "password = secret" in out
    assert "connect =" not in out  # 2.3-only syntax must be absent


def test_auth_sql_passdb_userdb_queries(render_dovecot):
    out = render_dovecot("auth-sql.conf.tpl")
    assert "passdb sql {" in out
    assert "userdb sql {" in out
    # Exact contract queries (2.4 %{user} variable).
    assert "SELECT password FROM users WHERE email = '%{user}' AND active" in out
    assert "5000 AS uid" in out
    assert "5000 AS gid" in out
    assert "split_part(email,'@',2)" in out
    assert "split_part(email,'@',1)" in out
    assert "quota_storage_size" in out


def test_auth_sql_weak_scheme_knobs(render_dovecot):
    out = render_dovecot("auth-sql.conf.tpl")
    assert "passdb_default_password_scheme = ARGON2ID" in out
    assert "auth_allow_weak_schemes = no" in out
    # When migration mode is on, the rendered value flips to yes.
    out_weak = render_dovecot(
        "auth-sql.conf.tpl",
        {"ALLOW_WEAK_SCHEMES": "true", "DOVECOT_AUTH_ALLOW_WEAK": "yes"},
    )
    assert "auth_allow_weak_schemes = yes" in out_weak


# ── F.2: 10-auth.conf ─────────────────────────────────────────────────────────

def test_auth_mechanisms_and_postfix_socket(render_dovecot):
    out = render_dovecot("10-auth.conf.tpl")
    # Plain + login are the only mechanisms (Postfix submission uses these).
    assert "auth_mechanisms = plain login" in out
    # SASL socket for Postfix lives under the postfix queue dir, mode 0660,
    # owned by postfix so smtpd can read it.
    assert "/var/spool/postfix/private/auth" in out
    assert "mode = 0660" in out
    assert "user = postfix" in out
    assert "group = postfix" in out
    # Pulls in the SQL passdb/userdb defined in F.1.
    assert "auth-sql.conf" in out
    # ARGON2ID needs a raised auth vsz limit.
    assert "vsz_limit" in out


def test_auth_pop3_gated_off_by_default(render_dovecot):
    out = render_dovecot("10-auth.conf.tpl")
    # POP3_ENABLED=false → no pop3 protocols/services injected.
    assert "pop3" not in out


def test_auth_pop3_gated_on(render_dovecot):
    out = render_dovecot(
        "10-auth.conf.tpl",
        {
            "POP3_ENABLED": "true",
            "DOVECOT_POP3_PROTOCOLS": " pop3",
            "DOVECOT_POP3_SERVICES": (
                "service pop3-login {\n"
                "  inet_listener pop3 { port = 110 }\n"
                "  inet_listener pop3s { port = 995 ssl = yes }\n"
                "}\n"
            ),
        },
    )
    assert "pop3-login" in out
    assert "port = 995" in out


# ── F.3: 10-mail.conf ─────────────────────────────────────────────────────────

def test_mail_location_maildir(render_dovecot):
    out = render_dovecot("10-mail.conf.tpl")
    # Maildir under each user's home (home comes from the SQL userdb).
    assert "maildir:~/Maildir" in out
    # vmail uid/gid 5000 owns the store.
    assert "mail_uid = 5000" in out
    assert "mail_gid = 5000" in out
    # Sieve namespace / first_valid_uid guard so Dovecot never runs as root.
    assert "first_valid_uid = 5000" in out


# ── F.4: 10-ssl.conf ──────────────────────────────────────────────────────────

def test_ssl_cert_key_and_min_protocol(render_dovecot):
    out = render_dovecot("10-ssl.conf.tpl")
    assert "ssl = yes" in out
    # 2.4 directive names; cert/key from TLS_* env.
    assert "ssl_server_cert_file = /tls/fullchain.pem" in out
    assert "ssl_server_key_file = /tls/privkey.pem" in out
    # TLS 1.2 floor.
    assert "ssl_min_protocol = TLSv1.2" in out
    assert "ssl_prefer_server_ciphers = yes" in out


# ── F.5: 15-lmtp.conf ─────────────────────────────────────────────────────────

def test_lmtp_listener_in_postfix_private(render_dovecot):
    out = render_dovecot("15-lmtp.conf.tpl")
    assert "service lmtp {" in out
    # Postfix delivers inbound mail over this socket inside its queue dir.
    assert "/var/spool/postfix/private/dovecot-lmtp" in out
    assert "mode = 0600" in out
    assert "user = postfix" in out
    # Sieve runs at LMTP delivery time.
    assert "sieve" in out
    assert "protocol lmtp {" in out


# ── F.6: 90-quota.conf ────────────────────────────────────────────────────────

def test_quota_count_driver_and_storage_size(render_dovecot):
    out = render_dovecot("90-quota.conf.tpl")
    # The quota plugin is enabled for the delivering/serving protocols.
    assert "quota" in out
    # Modern `count` backend (no separate dovecot-uidlist maintenance).
    assert "quota_driver = count" in out
    # Limit comes from the userdb's quota_storage_size field.
    assert "quota_storage_size" in out


# ── F.7: 20-managesieve.conf ──────────────────────────────────────────────────

def test_managesieve_port_4190(render_dovecot):
    out = render_dovecot("20-managesieve.conf.tpl")
    assert "service managesieve-login {" in out
    assert "inet_listener sieve {" in out
    assert "port = 4190" in out
    assert "protocol sieve {" in out
