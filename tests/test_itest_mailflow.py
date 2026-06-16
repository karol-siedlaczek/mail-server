"""Integration: submission AUTH -> LMTP -> Maildir -> IMAP (needs daemons).

Brought up by tests/compose.test.yml (phase A). Seed (tests/seed.sql, phase B)
provides domain example.test and users alice@example.test / bob@example.test
with ARGON2ID passwords. Run via `make itest`, never `make test`.
"""
import imaplib
import os
import ssl
import subprocess
import time

import pytest

# Use the phase-A conftest.py port constants (host-mapped ports from compose.test.yml).
from conftest import MAIL_HOST, SUBMISSION_PORT, IMAPS_PORT

ALICE = "alice@example.test"
# Contract (phase A / seed.sql): plaintext password is 'secret'.
ALICE_PW = os.environ.get("MAIL_TEST_ALICE_PW", "secret")


def _swaks(extra):
    cmd = [
        "swaks", "--server", MAIL_HOST, "--port", str(SUBMISSION_PORT),
        "--tls",  # STARTTLS on 587
    ] + extra
    return subprocess.run(cmd, capture_output=True, text=True)


def _imap_search(subject):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed test cert
    m = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
    try:
        m.login(ALICE, ALICE_PW)
        m.select("INBOX")
        # Poll: LMTP delivery is asynchronous to the SMTP 250.
        for _ in range(30):
            typ, data = m.search(None, "SUBJECT", f'"{subject}"')
            if typ == "OK" and data[0].split():
                return True
            time.sleep(1)
        return False
    finally:
        try:
            m.logout()
        except Exception:
            pass


@pytest.mark.integration
def test_submission_auth_then_lmtp_then_imap(compose):
    subject = f"itest-{int(time.time())}"
    r = _swaks([
        "--auth", "LOGIN", "--auth-user", ALICE, "--auth-password", ALICE_PW,
        "--from", ALICE, "--to", ALICE,
        "--header", f"Subject: {subject}",
        "--body", "phase-F integration",
    ])
    assert r.returncode == 0, r.stdout + r.stderr
    # Authenticated submission accepted (Dovecot SASL ok) ...
    assert "235" in r.stdout or "Authentication successful" in r.stdout
    assert " 250 " in r.stdout or "queued as" in r.stdout
    # ... and Postfix LMTP-delivered it to alice's Maildir, visible over IMAP.
    assert _imap_search(subject), "message not retrievable via IMAP"


@pytest.mark.integration
def test_submission_bad_password_rejected(compose):
    r = _swaks([
        "--auth", "LOGIN", "--auth-user", ALICE, "--auth-password", "wrong-pw",
        "--from", ALICE, "--to", ALICE,
        "--header", "Subject: should-fail",
        "--body", "nope",
    ])
    # Dovecot rejects the SASL bind; swaks reports the 535 and exits non-zero.
    assert r.returncode != 0
    assert "535" in (r.stdout + r.stderr) or "authentication failed" in (r.stdout + r.stderr).lower()
