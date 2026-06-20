"""End-to-end happy path for the mail-server image, driven against compose.

Scenarios (all asserted in one pass against the seeded stack):
  1. Inbound MX delivery -> IMAP read       (alice receives, reads over IMAPS)
  2. Authenticated submission -> external    (alice -> external@sink.test, sink gets it)
  3. DKIM signing on outbound                (sink copy carries DKIM-Signature: d=example.test)
  4. Forwarding redirect (no local copy)     (fwd@ -> external@sink.test; not in fwd's Maildir)
  5. Forwarding keep_copy                    (alice@ forward -> sink AND kept in alice Maildir)
  6. send-as grant                           (bob authenticates, MAIL FROM alice@ accepted)
  7. audit_logs                              (auth + send + delivery rows present)

Endpoints/credentials come from conftest. swaks drives SMTP; imaplib reads IMAP.

This module requires the live compose stack (marker: integration).
"""
import email
import imaplib
import re
import ssl
import subprocess
import time

import pytest

from conftest import (
    COMPOSE_FILE,
    MAIL_HOST,
    SMTP_PORT,
    SUBMISSION_PORT,
    IMAPS_PORT,
    read_sink,
    swaks as conftest_swaks,
)

pytestmark = pytest.mark.integration

DOMAIN = "example.test"
ALICE = f"alice@{DOMAIN}"
BOB = f"bob@{DOMAIN}"
FWD = f"fwd@{DOMAIN}"
EXTERNAL = "external@sink.test"  # matches seed.sql forwarding destinations
PASSWORD = "secret"  # seed.sql bakes ARGON2ID({secret}) for alice and bob


# --------------------------------------------------------------------------- #
# Session fixture: generate DKIM key once per session after stack is up
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session", autouse=True)
def _ensure_dkim_key(compose):
    """Generate the DKIM key for example.test (selector test) if missing.

    The seed.sql already sets domains.dkim_selector='test' and the rspamd
    maps point at /var/lib/rspamd/dkim/example.test.test.key. This key lives
    on the 'rspamd' volume which is empty on a fresh stack. We generate it
    once per session and reload rspamd so outbound mail is signed.
    """
    r = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "mail-server",
         "sh", "-c",
         "test -f /var/lib/rspamd/dkim/example.test.test.key || "
         "mail-dkim-keygen example.test test"],
        capture_output=True, text=True,
    )
    # Reload rspamd so it picks up the key (idempotent).
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "mail-server",
         "rspamadm", "control", "reload"],
        capture_output=True, text=True,
    )
    time.sleep(2)  # allow rspamd workers to reinitialise


# --------------------------------------------------------------------------- #
# Sink helpers (phase A: aiosmtpd, JSON-lines at /var/sink/messages.json)
# --------------------------------------------------------------------------- #

def _sink_reset():
    """Truncate /var/sink/messages.json on the running sink container."""
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "sink",
         "sh", "-c", "truncate -s 0 /var/sink/messages.json"],
        capture_output=True, text=True,
    )


def _get_sink_messages():
    """Return all messages from the sink as a list of parsed email objects."""
    records = read_sink()  # list of dicts with 'data' field (raw RFC 2822)
    return [email.message_from_string(r.get("data", "")) for r in records]


def _get_sink_records():
    """Return raw sink records (dicts: mail_from/rcpt_tos/data)."""
    return read_sink()


def _wait_for_sink(subject, want=1, deadline=60):
    """Poll the sink until at least `want` messages with `subject` appear."""
    end = time.time() + deadline
    last = []
    while time.time() < end:
        last = [m for m in _get_sink_messages()
                if subject in (m.get("Subject") or "")]
        if len(last) >= want:
            return last
        time.sleep(2)
    return last


def _send_inbound(to_addr, subject, body="inbound body"):
    """Deliver a message to port 25 (no auth) as if from the outside world."""
    r = conftest_swaks(
        to=to_addr,
        mail_from="sender@remote.test",
        server=MAIL_HOST,
        port=SMTP_PORT,
        body=body,
        header=[f"Subject: {subject}"],
        check=False,
    )
    return r


def _submit(from_addr, to_addr, subject, login, password=PASSWORD,
            body="submitted body"):
    """Authenticated submission on 587 with STARTTLS + SASL as `login`."""
    r = conftest_swaks(
        to=to_addr,
        mail_from=from_addr,
        server=MAIL_HOST,
        port=SUBMISSION_PORT,
        auth_user=login,
        auth_password=password,
        tls=True,
        body=body,
        header=[f"Subject: {subject}"],
        check=False,
    )
    return r


def _imap_subjects(user, password=PASSWORD, timeout=60):
    """Poll IMAPS INBOX for `user`; return the list of Subject headers seen."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed cert in tests
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx) as imap:
                imap.login(user, password)
                imap.select("INBOX")
                typ, data = imap.search(None, "ALL")
                subs = []
                for num in data[0].split():
                    typ, msg_data = imap.fetch(num, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])
                    subs.append(msg.get("Subject"))
                last = subs
                if subs:
                    return subs
        except imaplib.IMAP4.error:
            pass
        time.sleep(2)
    return last


def _audit_query(sql):
    """Run a read-only query against the compose Postgres via docker exec.

    Postgres is not host-published; connect via the container like read_sink().
    """
    r = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "postgres",
         "psql", "-U", "maildba", "-d", "maildb", "-tA", "-F", "|", "-c", sql],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"psql failed: {r.stderr}\n{r.stdout}"
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# 1. Inbound MX delivery -> IMAP read
# --------------------------------------------------------------------------- #
def test_inbound_delivery_then_imap_read(compose):
    subject = f"e2e-inbound-{int(time.time())}"
    r = _send_inbound(ALICE, subject)
    assert r.returncode == 0, r.stdout + r.stderr
    subs = _imap_subjects(ALICE)
    assert subject in subs, f"alice INBOX missing {subject!r}; saw {subs}"


# --------------------------------------------------------------------------- #
# 2 + 3. Authenticated submission -> external send (sink) with DKIM
# --------------------------------------------------------------------------- #
def test_authenticated_submission_to_external_with_dkim(compose):
    _sink_reset()
    subject = f"e2e-submit-{int(time.time())}"
    r = _submit(ALICE, EXTERNAL, subject, login=ALICE)
    assert r.returncode == 0, r.stdout + r.stderr

    msgs = _wait_for_sink(subject)
    assert msgs, f"sink never got subject {subject!r}"

    # Inspect raw message data for DKIM-Signature header.
    records = _get_sink_records()
    raw_msgs = [rec.get("data", "") for rec in records
                if subject in (email.message_from_string(
                    rec.get("data", "")).get("Subject") or "")]
    assert raw_msgs, "no matching raw messages found in sink"
    raw = raw_msgs[0]

    assert re.search(r"^DKIM-Signature:", raw, re.MULTILINE), (
        f"no DKIM-Signature header in outbound message:\n{raw[:800]}"
    )
    assert re.search(r"\bd=example\.test\b", raw), (
        f"DKIM signature not for d=example.test:\n{raw[:800]}"
    )


# --------------------------------------------------------------------------- #
# 4. Forwarding redirect (no local copy)
# --------------------------------------------------------------------------- #
def test_forwarding_redirect_no_local_copy(compose):
    _sink_reset()
    subject = f"e2e-fwd-redirect-{int(time.time())}"
    r = _send_inbound(FWD, subject)
    assert r.returncode == 0, r.stdout + r.stderr

    # Reached the external destination via the forwarding row.
    msgs = _wait_for_sink(subject)
    assert msgs, f"sink did not receive forwarded message {subject!r}"

    # fwd@ is NOT a real mailbox - IMAP login must fail.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    M = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
    try:
        with pytest.raises(imaplib.IMAP4.error):
            M.login(FWD, "whatever")
    finally:
        try:
            M.logout()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 5. Forwarding keep_copy
# --------------------------------------------------------------------------- #
def test_forwarding_keep_copy(compose):
    _sink_reset()
    subject = f"e2e-keepcopy-{int(time.time())}"
    # alice@ has a keep_copy=true forwarding to EXTERNAL in the seed.
    r = _send_inbound(ALICE, subject)
    assert r.returncode == 0, r.stdout + r.stderr

    # External copy arrived at sink.
    msgs = _wait_for_sink(subject)
    assert msgs, f"keep_copy did not forward to sink: {subject!r}"

    # Local copy kept in alice's Maildir, visible over IMAP.
    subs = _imap_subjects(ALICE)
    assert subject in subs, (
        f"keep_copy did not retain local copy for alice; saw {subs}"
    )


# --------------------------------------------------------------------------- #
# 6. send-as grant (bob may send as alice)
# --------------------------------------------------------------------------- #
def test_send_as_grant_allows_and_denies(compose):
    _sink_reset()
    ok_subject = f"e2e-sendas-ok-{int(time.time())}"
    # bob is granted to send AS alice -> accepted, delivered to sink.
    r = _submit(ALICE, EXTERNAL, ok_subject, login=BOB)
    assert r.returncode == 0, (
        f"expected bob->alice send-as to succeed, got rc={r.returncode}\n"
        f"{r.stdout}{r.stderr}"
    )
    msgs = _wait_for_sink(ok_subject)
    assert msgs, f"send-as message not delivered to sink: {ok_subject!r}"

    # bob has no grant to send AS fwd@ -> reject_sender_login_mismatch.
    r2 = _submit(FWD, EXTERNAL, "e2e-sendas-deny", login=BOB)
    assert r2.returncode != 0, (
        f"expected sender-login mismatch rejection for bob->fwd@, got rc=0\n"
        f"{r2.stdout}{r2.stderr}"
    )
    combined = r2.stdout + r2.stderr
    assert ("Sender address rejected" in combined
            or "5.7.1" in combined
            or "553" in combined), (
        f"expected 553/5.7.1 rejection message, got:\n{combined}"
    )


# --------------------------------------------------------------------------- #
# 7. audit_logs rows for auth + send + delivery
# --------------------------------------------------------------------------- #
def test_audit_logs_capture_auth_send_delivery(compose):
    # Drive one of each event so the rows exist for this run.
    subject = f"e2e-audit-{int(time.time())}"
    r = _submit(ALICE, EXTERNAL, subject, login=ALICE)
    assert r.returncode == 0, r.stdout + r.stderr

    # Wait for the submission to arrive at sink (confirms delivery path).
    _wait_for_sink(subject, deadline=60)

    # Also inject an inbound message for a delivery row.
    _send_inbound(ALICE, f"e2e-audit-in-{int(time.time())}")

    deadline = time.time() + 30
    got = set()
    while time.time() < deadline:
        rows = _audit_query("SELECT DISTINCT event_type FROM audit_logs")
        got = {r[0] for r in rows}
        if {"auth", "send", "delivery"} <= got:
            break
        time.sleep(2)
    assert {"auth", "send", "delivery"} <= got, (
        f"audit_logs missing event types; have {got}"
    )

    # A successful auth row for alice must be present.
    rows = _audit_query(
        "SELECT count(*) FROM audit_logs "
        "WHERE event_type='auth' AND success=true AND login='alice@example.test'"
    )
    assert rows and int(rows[0][0]) >= 1, "no successful auth row for alice"
