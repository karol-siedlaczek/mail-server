"""Phase H — forwarding / SRS / ARC integration tests (sink MTA).

Driven against tests/compose.test.yml (phase A). Mail is injected with swaks
over SMTP to the mail-server; forwarded copies are read back from the 'sink'
catch-all (aiosmtpd JSON-lines at /var/sink/messages.json, no HTTP); local
copies are read over IMAP with imaplib.

Only runs under `make itest` (marker: integration) which brings the compose
stack up first. Plain `make test` skips this module because no tests carry the
integration mark.
"""
import email
import imaplib
import os
import ssl
import subprocess
import time

import pytest

# Re-use phase-A conftest constants so we stay in sync with compose.test.yml.
from conftest import (
    COMPOSE_FILE,
    MAIL_HOST,
    SMTP_PORT,
    IMAPS_PORT,
    read_sink,
    swaks as conftest_swaks,
)

LOCAL_DOMAIN = "example.test"
# MAIL_HOSTNAME as set in compose.test.yml -> this is the SRS-envelope domain.
SRS_DOMAIN = "mail.example.test"

ALICE = f"alice@{LOCAL_DOMAIN}"
# Contract (phase A / seed.sql): plaintext password is 'secret'.
ALICE_PW = os.environ.get("MAIL_TEST_ALICE_PW", "secret")

FWD = f"fwd@{LOCAL_DOMAIN}"
EXTERNAL_SENDER = "outsider@remote.test"


# ── Sink helpers (phase A: aiosmtpd, JSON-lines, no HTTP) ────────────────────

def _get_sink_messages():
    """Return all messages from the sink as a list of parsed email objects."""
    records = read_sink()  # list of dicts with 'data' field (raw RFC 2822)
    return [email.message_from_string(r["data"]) for r in records]


def _sink_reset():
    """Truncate /var/sink/messages.json on the running sink container."""
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "sink",
         "sh", "-c", "truncate -s 0 /var/sink/messages.json"],
        capture_output=True, text=True,
    )


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


def _imap_count(user, password, subject, deadline=60):
    """Poll IMAP until a message with `subject` is visible in INBOX."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed test cert
    end = time.time() + deadline
    while time.time() < end:
        M = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
        try:
            M.login(user, password)
            M.select("INBOX")
            typ, data = M.search(None, "SUBJECT", f'"{subject}"')
            ids = data[0].split() if (typ == "OK" and data and data[0]) else []
            if ids:
                return len(ids)
        except imaplib.IMAP4.error:
            pass
        finally:
            try:
                M.logout()
            except Exception:
                pass
        time.sleep(2)
    return 0


def _inject(to, frm, subject):
    """Send one message via SMTP port 25 (no auth) using the conftest swaks helper."""
    r = conftest_swaks(
        to=to,
        mail_from=frm,
        server=MAIL_HOST,
        port=SMTP_PORT,
        body=f"itest body {subject}",
        header=[f"Subject: {subject}"],
        check=False,
    )
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_plain_forward_redirects_to_sink_with_srs_sender_no_local_copy(compose):
    """Plain forwarding fwd@ -> sink: redirected, envelope sender SRS-rewritten."""
    _sink_reset()
    subj = f"H4-plain-{int(time.time())}"
    r = _inject(FWD, EXTERNAL_SENDER, subj)
    assert r.returncode == 0, r.stderr or r.stdout

    msgs = _wait_for_sink(subj, want=1)
    assert len(msgs) == 1, f"expected forwarded copy at sink, got {len(msgs)}"

    # Envelope sender must be SRS-rewritten.  Postfix stamps it as Return-Path.
    rp = (msgs[0].get("Return-Path") or "").strip("<>").lower()
    assert rp.startswith("srs0=") or rp.startswith("srs1="), (
        f"sender not SRS-rewritten: {rp!r}"
    )
    assert rp.endswith("@" + SRS_DOMAIN.lower()), (
        f"SRS sender not in local domain ({SRS_DOMAIN}): {rp!r}"
    )

    # fwd@ is NOT a real mailbox — IMAP login must fail.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    M = imaplib.IMAP4_SSL(MAIL_HOST, IMAPS_PORT, ssl_context=ctx)
    with pytest.raises(imaplib.IMAP4.error):
        M.login(FWD, "whatever")
    try:
        M.logout()
    except Exception:
        pass


@pytest.mark.integration
def test_keep_copy_delivers_local_and_forwards_to_sink(compose):
    """keep_copy row (alice@): local Maildir copy AND a copy at the sink."""
    _sink_reset()
    subj = f"H4-keepcopy-{int(time.time())}"
    # alice@ has a keep_copy=true forwarding to external@sink.test (seed.sql)
    # and is a real mailbox.
    r = _inject(ALICE, EXTERNAL_SENDER, subj)
    assert r.returncode == 0, r.stderr or r.stdout

    # Local copy visible over IMAP.
    count = _imap_count(ALICE, ALICE_PW, subj)
    assert count >= 1, "no local copy delivered for keep_copy row"

    # Forwarded copy at the sink.
    msgs = _wait_for_sink(subj, want=1)
    assert len(msgs) >= 1, "keep_copy did not forward a copy to the sink"


@pytest.mark.integration
def test_forwarded_copy_carries_arc_seal_header(compose):
    """Forwarded mail carries ARC-Seal + ARC-Message-Signature (rspamd arc.conf)."""
    _sink_reset()
    subj = f"H4-arc-{int(time.time())}"
    r = _inject(FWD, EXTERNAL_SENDER, subj)
    assert r.returncode == 0, r.stderr or r.stdout

    msgs = _wait_for_sink(subj, want=1)
    assert len(msgs) == 1, "no forwarded copy to inspect for ARC"

    # Rspamd arc.conf sign_inbound=true (phase G) seals forwarded mail.
    assert msgs[0].get("ARC-Seal") is not None, (
        "forwarded copy missing ARC-Seal header"
    )
    assert msgs[0].get("ARC-Message-Signature") is not None, (
        "forwarded copy missing ARC-Message-Signature"
    )
