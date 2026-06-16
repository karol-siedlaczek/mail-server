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
