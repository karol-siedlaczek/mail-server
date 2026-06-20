"""Integration: audit_logs is populated for auth failures and authenticated
sends. Runs against the compose.test.yml stack via the Makefile 'itest' target
(the `compose` session fixture brings the stack up).

Reconciliation notes (phase A is the source of truth):
- Alice's password is 'secret' (seed.sql ARGON2ID hash).
- Postgres is NOT published to the host, so audit_logs is read via
  `docker compose exec -T postgres psql` (the same shell-exec pattern read_sink
  uses for the sink), with the compose creds (db=maildb, user=maildba).
- Mail ports are host-mapped (IMAPS 12993, submission 12587) via conftest.
- The 'send' row's sender may be SRS-rewritten depending on domain config, so
  we assert the authenticated login + message_id + queue_id (the attribution
  that proves send-auditing), not the exact envelope sender.
"""
import imaplib
import shutil
import ssl
import subprocess
import time

import pytest

from conftest import COMPOSE_FILE, MAIL_HOST, IMAPS_PORT, SUBMISSION_PORT

pytestmark = pytest.mark.integration

ALICE = "alice@example.test"
ALICE_PW = "secret"


def _audit_query(sql):
    """Run a read-only query against the compose Postgres and return rows as a
    list of tuples (split on '|'). Postgres isn't host-published, so go through
    the container like read_sink() does for the sink."""
    r = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "postgres",
         "psql", "-U", "maildba", "-d", "maildb", "-tA", "-F", "|", "-c", sql],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"psql failed: {r.stderr}\n{r.stdout}"
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip()]


def _wait_for_audit(sql, timeout=30):
    """Poll the audit query until it returns at least one row, or timeout."""
    deadline = time.time() + timeout
    rows = []
    while time.time() < deadline:
        rows = _audit_query(sql)
        if rows:
            return rows
        time.sleep(1)
    return rows


def _find_swaks():
    return shutil.which("swaks")


def test_failed_imap_login_writes_auth_failure_row(compose):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed test cert
    cli = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
    with pytest.raises(imaplib.IMAP4.error):
        cli.login(ALICE, "definitely-wrong-password")
    try:
        cli.logout()
    except Exception:
        pass

    rows = _wait_for_audit(
        "SELECT success, login, host(src_ip) FROM audit_logs "
        "WHERE event_type='auth' AND login='%s' AND success=false "
        "ORDER BY \"timestamp\" DESC LIMIT 1" % ALICE
    )
    assert rows, "no auth-failure audit row was written"
    success, login, src_ip = rows[0]
    assert success == "f"
    assert login == ALICE
    assert src_ip  # source IP captured


def test_successful_submission_send_writes_send_row(compose):
    exe = _find_swaks()
    if exe is None:
        pytest.skip("swaks not installed")
    subprocess.run(
        [exe, "--server", MAIL_HOST, "--port", str(SUBMISSION_PORT), "--tls",
         "--auth", "PLAIN", "--auth-user", ALICE, "--auth-password", ALICE_PW,
         "--from", ALICE, "--to", "dest@external.test",
         "--h-Subject", "audit-send-test", "--body", "audit integration send"],
        check=True, timeout=60,
    )

    rows = _wait_for_audit(
        "SELECT login, (message_id IS NOT NULL), (queue_id IS NOT NULL) "
        "FROM audit_logs WHERE event_type='send' AND login='%s' "
        "ORDER BY \"timestamp\" DESC LIMIT 1" % ALICE
    )
    assert rows, "no send audit row was written for the authenticated login"
    login, has_msgid, has_qid = rows[0]
    assert login == ALICE
    assert has_msgid == "t", "send row missing message_id"
    assert has_qid == "t", "send row missing queue_id"
