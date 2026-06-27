"""Integration: Rspamd DKIM signing + spam rejection against the compose stack.

Brought up by `make itest` (compose.test.yml).  The mail
server is reachable on the compose-published ports; this test drives SMTP with
swaks and reads delivered mail over IMAP with imaplib.  Seed data (phase A
tests/seed.sql): domain example.test (dkim_selector 'test'), alice@example.test
and bob@example.test with known passwords (plaintext: 'secret'), a DKIM key for
the seeded selector generated at test-setup if not already present.

Reconciled from task G.7 spec to phase A real values:
  - ports: SUBMISSION_PORT=12587, SMTP_PORT=12525, IMAPS_PORT=12993 (conftest.py)
  - password: 'secret' (seed.sql ARGON2ID hash)
  - IMAP access via IMAPS (self-signed cert, verify disabled)
  - GTUBE rejection on submission port (postscreen on :25 blocks unauthenticated
    connections from unknown IPs on first attempt; submission has no postscreen)
  - Authentication-Results: ARC-Authentication-Results is the header rspamd adds
    for outbound/local mail; the assertion accepts either form.
"""
import email
import imaplib
import os
import shutil
import ssl
import subprocess
import time

import pytest

from conftest import MAIL_HOST, SUBMISSION_PORT, SMTP_PORT, IMAPS_PORT, COMPOSE_FILE

ALICE = "alice@example.test"
# Seed password (seed.sql, phase A): plaintext 'secret'
ALICE_PW = os.environ.get("MAIL_TEST_ALICE_PW", "secret")
BOB = "bob@example.test"
BOB_PW = os.environ.get("MAIL_TEST_BOB_PW", "secret")

GTUBE = "XJS*C4JDBQADN1.NSBN3*2IDNEN*GTUBE-STANDARD-ANTI-UBE-TEST-EMAIL*C.34X"


def _ensure_dkim_key(domain="example.test", selector="test"):
    """Generate the DKIM key inside the running container if it does not exist.

    render-config renders the selectors.map and paths.map pointing to
    /var/lib/rspamd/dkim/<domain>.<selector>.key, but does not auto-generate the
    key (operators normally run mail-dkim-keygen once per domain).  For the
    integration test we generate the key and reload rspamd so that
    dkim_signing.conf can actually sign outbound mail.
    """
    key_path = f"/var/lib/rspamd/dkim/{domain}.{selector}.key"
    # Check whether the key already exists.
    check = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T",
         "mail-server", "test", "-f", key_path],
        capture_output=True,
    )
    if check.returncode == 0:
        return  # key already present

    # Generate key with rspamadm inside the container.
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T",
         "mail-server", "bash", "-c",
         f"mkdir -p /var/lib/rspamd/dkim && "
         f"rspamadm dkim_keygen -d {domain} -s {selector} "
         f"-k {key_path} -b 2048 && "
         f"chown root:_rspamd {key_path} && "
         f"chmod 0640 {key_path}"],
        capture_output=True, check=True,
    )
    # Signal rspamd to reload its config so it picks up the new key.
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T",
         "mail-server", "bash", "-c", "pkill -HUP rspamd 2>/dev/null || true"],
        capture_output=True,
    )
    # Wait for rspamd to finish reloading: poll rspamadm configtest (which
    # succeeds only when rspamd's worker is fully restarted and the key is
    # loaded) rather than sleeping for an arbitrary duration.
    deadline = time.time() + 30
    while time.time() < deadline:
        check = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T",
             "mail-server", "rspamadm", "configtest"],
            capture_output=True,
        )
        if check.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("rspamd did not pass configtest within 30s after HUP")


def _imap_fetch_by_subject(subject, user=ALICE, pw=ALICE_PW, timeout=30):
    """Poll IMAPS until a message with the given subject appears; return it."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
        try:
            m.login(user, pw)
            m.select("INBOX")
            typ, data = m.search(None, "SUBJECT", f'"{subject}"')
            ids = data[0].split() if typ == "OK" else []
            if ids:
                typ, msg_data = m.fetch(ids[-1], "(RFC822)")
                return email.message_from_bytes(msg_data[0][1])
        finally:
            try:
                m.logout()
            except Exception:
                pass
        time.sleep(2)
    return None


def _swaks(*args):
    """Run swaks with the given CLI arguments; return CompletedProcess."""
    exe = shutil.which("swaks") or os.path.expanduser("~/.local/bin/swaks")
    return subprocess.run([exe, *args], capture_output=True, text=True)


@pytest.mark.integration
def test_submission_is_dkim_signed(compose):
    """Alice authenticates on submission (587); Rspamd must add a
    DKIM-Signature aligned to her From domain.  ARC-Authentication-Results
    (added by the ARC module) confirms rspamd processed the message."""
    _ensure_dkim_key()

    subject = f"dkim-itest-{int(time.time())}"
    r = _swaks(
        "--server", f"{MAIL_HOST}:{SUBMISSION_PORT}",
        "--tls",
        "--auth", "LOGIN", "--auth-user", ALICE, "--auth-password", ALICE_PW,
        "--from", ALICE, "--to", ALICE,
        "--h-Subject", subject,
        "--body", "hello dkim integration test",
    )
    assert "250" in r.stdout, (
        f"submission failed:\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    msg = _imap_fetch_by_subject(subject)
    assert msg is not None, "message not delivered to alice within timeout"

    sigs = msg.get_all("DKIM-Signature") or []
    assert sigs, "no DKIM-Signature header added by Rspamd"
    joined = " ".join(sigs)
    assert "d=example.test" in joined, (
        f"DKIM not aligned to From domain (want d=example.test): {joined}"
    )
    assert "s=test" in joined, (
        f"unexpected selector (want s=test): {joined}"
    )

    # Rspamd's ARC module seals the outbound message and adds
    # ARC-Authentication-Results; the plain Authentication-Results header is
    # only added on the inbound path (SPF/DKIM/DMARC verification).
    arc_ar = msg.get("ARC-Authentication-Results")
    plain_ar = msg.get("Authentication-Results")
    assert arc_ar or plain_ar, (
        "no Authentication-Results / ARC-Authentication-Results header "
        "(Rspamd did not process the message through the milter)"
    )


@pytest.mark.integration
def test_gtube_is_rejected(compose):
    """A message whose body contains the GTUBE pattern must be rejected by
    Rspamd at SMTP DATA time with a 554/550 response.

    We use the submission port (587) with authentication because postscreen
    on port 25 blocks unauthenticated connections from unknown IPs on the
    first attempt; submission bypasses postscreen and always reaches smtpd."""
    _ensure_dkim_key()

    r = _swaks(
        "--server", f"{MAIL_HOST}:{SUBMISSION_PORT}",
        "--tls",
        "--auth", "LOGIN", "--auth-user", ALICE, "--auth-password", ALICE_PW,
        "--from", ALICE, "--to", ALICE,
        "--h-Subject", "gtube-itest",
        "--body", GTUBE,
    )
    out = r.stdout + r.stderr
    # Rspamd's 'reject' action for GTUBE (score >> reject threshold) causes
    # Postfix to return a 554 5.7.1 error at DATA time.
    assert ("554" in out or "550" in out or "rejected" in out.lower()), (
        f"GTUBE was not rejected (want 554/550):\n{out}"
    )
