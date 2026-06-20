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

REPO = Path(__file__).resolve().parents[3]
IMG = REPO / "images" / "mail-server"
RENDER = IMG / "rootfs" / "usr" / "local" / "bin" / "render-config.sh"

BASE_ENV = {
    "MAIL_HOSTNAME": "mail.example.test",
    "PG_HOST": "pg", "PG_PORT": "5432", "PG_DBNAME": "mail",
    "PG_USER": "mail_ro", "PG_PASSWORD": "ropw",
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
