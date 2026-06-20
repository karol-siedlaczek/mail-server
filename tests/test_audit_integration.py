"""Integration: audit_logs is populated for auth failures and authenticated
sends. Requires the compose.test.yml stack (mail-server + postgres + redis)
to be up; run via the Makefile 'itest' target. Skipped if the stack env is
absent so the unit-only 'test' target stays green.

Reconciliation notes (phase A is the source of truth):
- Alice's password is 'secret' (seed.sql ARGON2ID hash), not 'alice-test-pw'.
- PG coordinates default to phase A's compose.test.yml values: db=maildb,
  user=maildba, password=testpw; the mail_audit role is seeded by seed.sql.
- Ports are host-mapped (12993/12587), not container-internal (993/587).
- swaks is at ~/.local/bin/swaks; shutil.which falls back to that path.
"""
import imaplib
import os
import shutil
import subprocess
import time

import pytest

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None


def _env(name, default=None):
    return os.environ.get(name, default)


def _stack_available():
    return psycopg2 is not None and _env("PG_HOST") and _env("MAIL_HOST")


pytestmark = pytest.mark.skipif(
    not _stack_available(),
    reason="integration stack env not set (PG_HOST/MAIL_HOST); run via 'make itest'",
)

MAIL_HOST = _env("MAIL_HOST", "127.0.0.1")
# Host-mapped ports from compose.test.yml (phase A): 12993 -> 993, 12587 -> 587.
IMAPS_PORT = int(_env("IMAPS_PORT", "12993"))
SUBMISSION_PORT = int(_env("SUBMISSION_PORT", "12587"))


def _db():
    # Read as the maildba superuser so we can see rows written by mail_audit.
    return psycopg2.connect(
        host=_env("PG_HOST"),
        port=_env("PG_PORT", "5432"),
        dbname=_env("PG_DBNAME", "maildb"),
        user=_env("PG_USER", "maildba"),
        password=_env("PG_PASSWORD", "testpw"),
    )


def _wait_for_row(query, params, timeout=30):
    """Poll the DB until the query returns a row or timeout. Returns the row."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        with _db() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            last = cur.fetchone()
            if last:
                return last
        time.sleep(1)
    return last


def _find_swaks():
    """Locate swaks: PATH first, then the known host install path."""
    exe = shutil.which("swaks")
    if exe:
        return exe
    fallback = os.path.expanduser("~/.local/bin/swaks")
    if os.path.isfile(fallback):
        return fallback
    return None


def test_failed_imap_login_writes_auth_failure_row():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed test cert
    cli = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
    with pytest.raises(imaplib.IMAP4.error):
        cli.login("alice@example.test", "definitely-wrong-password")
    try:
        cli.logout()
    except Exception:
        pass

    row = _wait_for_row(
        """SELECT success, login, src_ip, host
             FROM audit_logs
            WHERE event_type='auth' AND login=%s AND success=false
            ORDER BY "timestamp" DESC LIMIT 1""",
        ("alice@example.test",),
    )
    assert row is not None, "no auth failure audit row was written"
    success, login, src_ip, host = row
    assert success is False
    assert login == "alice@example.test"
    assert src_ip is not None


def test_successful_submission_send_writes_send_row():
    exe = _find_swaks()
    if exe is None:
        pytest.skip("swaks not installed")
    # Phase A seed: alice@example.test password is 'secret' (ARGON2ID hash).
    subprocess.run(
        [
            exe,
            "--server", MAIL_HOST,
            "--port", str(SUBMISSION_PORT),
            "--tls",
            "--auth", "PLAIN",
            "--auth-user", "alice@example.test",
            "--auth-password", "secret",
            "--from", "alice@example.test",
            "--to", "dest@external.test",
            "--h-Subject", "audit-send-test",
            "--body", "audit integration send",
        ],
        check=True,
        timeout=60,
    )

    row = _wait_for_row(
        """SELECT login, sender, message_id, queue_id
             FROM audit_logs
            WHERE event_type='send' AND login=%s AND sender=%s
            ORDER BY "timestamp" DESC LIMIT 1""",
        ("alice@example.test", "alice@example.test"),
    )
    assert row is not None, "no send audit row was written"
    login, sender, message_id, queue_id = row
    assert login == "alice@example.test"
    assert sender == "alice@example.test"
    assert message_id is not None
    assert queue_id is not None
