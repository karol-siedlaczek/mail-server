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
    assert "user = mail-server-ro_user" in out
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


# ── J.3: AUDIT_POLICY_BLOCK rendering in 10-auth.conf ────────────────────────

def test_audit_policy_block_rendered_when_enabled(render_dovecot):
    """When AUDIT_POLICY_BLOCK is set, 10-auth.conf contains the auth-policy stanza."""
    block = (
        "auth_policy_server_url = http://127.0.0.1:4001/\n"
        "auth_policy_hash_nonce = deadbeef\n"
        "auth_policy_report_after_auth = yes\n"
    )
    out = render_dovecot("10-auth.conf.tpl", {"AUDIT_POLICY_BLOCK": block})
    assert "auth_policy_server_url = http://127.0.0.1:4001/" in out
    assert "auth_policy_hash_nonce = deadbeef" in out
    assert "auth_policy_report_after_auth = yes" in out


def test_audit_policy_block_absent_when_disabled(render_dovecot):
    """When AUDIT_POLICY_BLOCK is empty, 10-auth.conf contains no auth-policy URL."""
    out = render_dovecot("10-auth.conf.tpl", {"AUDIT_POLICY_BLOCK": ""})
    assert "auth_policy_server_url" not in out


# ── F.3: 10-mail.conf ─────────────────────────────────────────────────────────

def test_mail_location_maildir(render_dovecot):
    out = render_dovecot("10-mail.conf.tpl")
    # Dovecot 2.4 split `mail_location = maildir:~/Maildir` into two directives:
    assert "mail_driver = maildir" in out
    assert "mail_path = ~/Maildir" in out
    # The legacy combined form must NOT appear as an active directive.
    active_lines = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("mail_location" in ln for ln in active_lines)
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
    # ssl_prefer_server_ciphers was removed in Dovecot 2.4 — must NOT appear
    # as an active (non-commented) directive.
    active_lines = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("ssl_prefer_server_ciphers" in ln for ln in active_lines)


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
    # Dovecot 2.4 `count` backend: configured via `driver = count` inside the
    # named quota block, NOT the legacy top-level `quota_driver = count`.
    assert 'driver = count' in out
    assert 'quota "' in out  # named quota block is present
    # The legacy flat directive must NOT appear as an active line.
    active_lines = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.strip().startswith("quota_driver") for ln in active_lines)
    # NOTE: the per-user limit comes from the userdb query's quota_storage_size
    # field (auth-sql.conf), not from this file — verified in the F.9 integration
    # test, so it is intentionally not asserted against 90-quota.conf here.


# ── F.7: 20-managesieve.conf ──────────────────────────────────────────────────

def test_managesieve_port_4190(render_dovecot):
    out = render_dovecot("20-managesieve.conf.tpl")
    assert "service managesieve-login {" in out
    assert "inet_listener sieve {" in out
    assert "port = 4190" in out
    assert "protocol sieve {" in out


# ── F.8: doveconf -n validation ───────────────────────────────────────────────

# Top-level conf.d fragments (included by glob from dovecot.conf root).
# auth-sql.conf is NOT in this list: it is an !include'd sub-file pulled in by
# 10-auth.conf directly; adding it to the glob would cause a "recursive include"
# error from doveconf. It is written alongside the other conf.d files so the
# relative !include path resolves correctly.
DOVECOT_CONFD_TEMPLATES = [
    "10-auth.conf.tpl",
    "10-mail.conf.tpl",
    "10-ssl.conf.tpl",
    "15-lmtp.conf.tpl",
    "90-quota.conf.tpl",
    "20-managesieve.conf.tpl",
]

DOVECOT_TEMPLATES = ["auth-sql.conf.tpl"] + DOVECOT_CONFD_TEMPLATES


@pytest.mark.skipif(shutil.which("doveconf") is None, reason="doveconf not installed")
def test_doveconf_n_parses_rendered_config(render_dovecot, tmp_path):
    confd = tmp_path / "conf.d"
    confd.mkdir()
    # Write auth-sql.conf into conf.d/ so the relative !include in 10-auth.conf
    # resolves it, but do NOT include it in the root glob (it would be double-
    # included and doveconf would report "Recursive include file").
    (confd / "auth-sql.conf").write_text(render_dovecot("auth-sql.conf.tpl"))
    for tpl in DOVECOT_CONFD_TEMPLATES:
        text = render_dovecot(tpl)
        (confd / tpl.replace(".tpl", "")).write_text(text)
    # Minimal root that pulls in the rendered fragments, the way the image's
    # /etc/dovecot/dovecot.conf does. Dovecot 2.4 requires dovecot_config_version
    # as the very first setting. The glob picks up the conf.d/ files but NOT
    # auth-sql.conf (it is included by 10-auth.conf via !include auth-sql.conf).
    # Use individual !include lines to avoid globbing auth-sql.conf (which is
    # already included by 10-auth.conf and must not be double-included via glob).
    include_lines = "\n".join(
        "!include %s" % str(confd / tpl.replace(".tpl", ""))
        for tpl in DOVECOT_CONFD_TEMPLATES
    )
    root = tmp_path / "dovecot.conf"
    root.write_text(
        "dovecot_config_version = 2.4.0\n"
        "dovecot_storage_version = 2.4.0\n"
        "base_dir = %s\n"
        "%s\n" % (tmp_path / "run", include_lines)
    )
    proc = subprocess.run(
        ["doveconf", "-n", "-c", str(root)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # A few normalized keys must survive doveconf's own re-serialization.
    assert "auth_mechanisms = plain login" in proc.stdout
    # ssl_min_protocol = TLSv1.2 — rendered in template; doveconf -n omits
    # it when it equals the compiled-in default, so we verify the SSL block
    # is present instead (cert/key are always non-default).
    assert "ssl_server" in proc.stdout or "ssl_min_protocol" in proc.stdout
