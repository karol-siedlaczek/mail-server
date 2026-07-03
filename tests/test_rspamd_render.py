"""Render the Rspamd local.d templates with a fixed env and assert their content.

Pure render test (no daemons): runs the image's render-config.sh with output
roots overridden to a tmpdir via RSPAMD_LOCALD_DIR / RSPAMD_DKIM_DIR so the
generated files land where pytest can read them.  Phase A's render-config.sh
MUST honour those two override vars; see cross_phase_notes.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]   # repo root == image build context
IMG = REPO
RENDER = IMG / "rootfs" / "usr" / "local" / "bin" / "render-config.sh"

BASE_ENV = {
    "MAIL_HOSTNAME": "mail.example.test",
    "PG_HOST": "pg", "PG_PORT": "5432", "PG_DBNAME": "mail",
    "PG_USER": "mail-server-ro_user", "PG_PASSWORD": "ropw",
    "REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_DB": "3",
    "REDIS_PREFIX": "ml", "REDIS_PASSWORD": "secretredis",
    "RSPAMD_REJECT_SCORE": "12",
    "CLAMAV_ENABLED": "true", "CLAMAV_HOST": "clamav", "CLAMAV_PORT": "3310",
    "DMARC_REPORT_ENABLED": "true", "DMARC_REPORT_EMAIL": "dmarc@example.test",
    "TLS_CERT_FILE": "/tls/fullchain.pem", "TLS_KEY_FILE": "/tls/privkey.pem",
}


@pytest.fixture
def rendered(tmp_path):
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True)
    dkimdir.mkdir(parents=True)
    env = dict(os.environ)
    env.update(BASE_ENV)
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    # PSYCOPG-free: a render test must not hit Postgres.  render-config.sh
    # skips the dkim map SELECT when RSPAMD_SKIP_DB=1 (phase A contract).
    env["RSPAMD_SKIP_DB"] = "1"
    # RENDER_ROOT redirects the generic tpl/render.map outputs to tmpdir so the
    # test runs unprivileged (no writes to /etc/postfix etc.).
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    return localdir

def read(rendered, name):
    return (rendered / name).read_text()

def test_worker_proxy(rendered):
    t = read(rendered, "worker-proxy.inc")
    assert 'bind_socket = "*:11332"' in t
    assert "milter = yes" in t
    assert "self_scan = yes" in t
    assert "upstream" not in t  # self-scan proxy: no separate scanner upstream

def test_actions(rendered):
    t = read(rendered, "actions.conf")
    assert "greylist = 4;" in t
    assert "add_header = 6;" in t
    assert "reject = 12;" in t  # RSPAMD_REJECT_SCORE

def test_redis(rendered):
    t = read(rendered, "redis.conf")
    assert 'servers = "redis:6379";' in t
    assert "db = '3';" in t
    assert 'password = "secretredis";' in t
    # BASE_ENV sets no REDIS_USERNAME → legacy password-only AUTH; no username
    # directive emitted (an empty username would make Rspamd send AUTH "" <pass>).
    assert "username =" not in t
    # Per-module key prefixes derived from REDIS_PREFIX (Rspamd has no single
    # global prefix), set consistently for bayes/greylist/ratelimit/dkim.
    assert 'key_prefix = "ml_bayes";' in t
    assert 'greylist { key_prefix = "ml_greylist"; }' in t.replace("\n", " ") \
        or 'key_prefix = "ml_greylist";' in t
    assert 'key_prefix = "ml_ratelimit";' in t
    assert 'key_prefix = "ml_dkim";' in t

def test_redis_acl_username(tmp_path):
    """REDIS_USERNAME set → Redis 6+ ACL login: username AND password emitted."""
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True); dkimdir.mkdir(parents=True)
    env = dict(os.environ); env.update(BASE_ENV)
    env["REDIS_USERNAME"] = "rspamd"
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    env["RSPAMD_SKIP_DB"] = "1"
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    t = (localdir / "redis.conf").read_text()
    assert 'username = "rspamd";' in t
    assert 'password = "secretredis";' in t


def test_spf(rendered):
    t = read(rendered, "spf.conf")
    assert "spf" in t.lower()
    # whitelist/disabled toggles left at defaults; module simply present/enabled.
    assert "disabled = false;" in t

def test_dkim_signing(rendered):
    t = read(rendered, "dkim_signing.conf")
    assert "sign_authenticated = true;" in t
    assert "sign_local = true;" in t
    assert "use_domain = \"header\";" in t
    assert "try_fallback = false;" in t
    assert 'selector_map = "/etc/rspamd/dkim/selectors.map";' in t
    assert 'path_map = "/etc/rspamd/dkim/paths.map";' in t

def test_arc(rendered):
    t = read(rendered, "arc.conf")
    assert "sign_inbound = true;" in t
    assert "sign_authenticated = true;" in t
    assert 'selector_map = "/etc/rspamd/dkim/selectors.map";' in t
    assert 'path_map = "/etc/rspamd/dkim/paths.map";' in t

def test_dmarc_reporting_enabled(rendered):
    # BASE_ENV sets DMARC_REPORT_ENABLED=true.
    t = read(rendered, "dmarc.conf")
    assert "reporting {" in t
    assert "enabled = true;" in t
    assert 'email = "dmarc@example.test";' in t
    assert 'from_name = "mail.example.test";' in t or 'org_name' in t

def test_dmarc_reporting_disabled(tmp_path):
    # Re-render with DMARC_REPORT_ENABLED=false and assert reporting is off.
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True); dkimdir.mkdir(parents=True)
    env = dict(os.environ); env.update(BASE_ENV)
    env["DMARC_REPORT_ENABLED"] = "false"
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    env["RSPAMD_SKIP_DB"] = "1"
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    t = (localdir / "dmarc.conf").read_text()
    assert "enabled = false;" in t

def test_antivirus_enabled(rendered):
    t = read(rendered, "antivirus.conf")
    assert 'type = "clamav";' in t
    assert 'servers = "clamav:3310";' in t

def test_antivirus_disabled(tmp_path):
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True); dkimdir.mkdir(parents=True)
    env = dict(os.environ); env.update(BASE_ENV)
    env["CLAMAV_ENABLED"] = "false"
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    env["RSPAMD_SKIP_DB"] = "1"
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    t = (localdir / "antivirus.conf").read_text()
    # Disabled cleanly: module switched off, no clamav server line.
    assert "clamav { enabled = false; }" in t.replace("\n", " ") \
        or "enabled = false;" in t

def test_dkim_maps_rendered_from_db(tmp_path):
    """When not skipping the DB, render-config writes selectors.map/paths.map
    from `SELECT domain, dkim_selector FROM domains WHERE active`.

    We don't stand up Postgres in the unit test: instead we feed render-config a
    pre-seeded query result via RSPAMD_DKIM_ROWS (one 'domain selector' per line),
    a hook render-config honours so the map rendering is unit-testable without a
    live DB.  (Integration coverage hits a real Postgres in itest_rspamd.py.)
    """
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True); dkimdir.mkdir(parents=True)
    env = dict(os.environ); env.update(BASE_ENV)
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    env["RSPAMD_SKIP_DB"] = "1"  # skip the live SELECT
    env["RSPAMD_DKIM_ROWS"] = "example.test test\nfoo.test default"
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    sel = (dkimdir / "selectors.map").read_text()
    paths = (dkimdir / "paths.map").read_text()
    assert "example.test test" in sel
    assert "foo.test default" in sel
    assert "example.test /var/lib/rspamd/dkim/example.test.test.key" in paths
    assert "foo.test /var/lib/rspamd/dkim/foo.test.default.key" in paths


def test_controller_localhost_by_default(rendered):
    """No RSPAMD_CONTROLLER_PASSWORD → controller stays bound to 127.0.0.1."""
    t = read(rendered, "worker-controller.inc")
    assert 'bind_socket = "127.0.0.1:11334";' in t
    assert '"*:11334"' not in t


def test_controller_exposed_with_password(tmp_path):
    """RSPAMD_CONTROLLER_PASSWORD (pre-hashed) → controller binds *:11334 with
    the password, so a reverse proxy / HAProxy backend can reach it."""
    localdir = tmp_path / "rspamd" / "local.d"
    dkimdir = tmp_path / "rspamd" / "dkim"
    localdir.mkdir(parents=True); dkimdir.mkdir(parents=True)
    env = dict(os.environ); env.update(BASE_ENV)
    # an already-hashed value ('$...') is injected verbatim (no rspamadm needed)
    env["RSPAMD_CONTROLLER_PASSWORD"] = "$2$abc123$deadbeefcafe"
    env["RSPAMD_LOCALD_DIR"] = str(localdir)
    env["RSPAMD_DKIM_DIR"] = str(dkimdir)
    env["RSPAMD_SKIP_DB"] = "1"
    env["RENDER_ROOT"] = str(tmp_path / "render_root")
    subprocess.run(["bash", str(RENDER)], env=env, check=True,
                   cwd=str(REPO), capture_output=True)
    t = (localdir / "worker-controller.inc").read_text()
    assert 'bind_socket = "*:11334";' in t
    assert 'password = "$2$abc123$deadbeefcafe";' in t
    assert 'enable_password = "$2$abc123$deadbeefcafe";' in t
