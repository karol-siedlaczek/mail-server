"""Phase K integration assertions against the live compose stack.

Run via `make -C images/mail-server itest` (which brings up
tests/compose.test.yml: the built image + postgres + redis + sink, seeded with
sql/schema.sql + tests/seed.sql). These tests talk to the running container.

Reconciliation notes (Phase A contract):
- Ports are the compose.test.yml host-mapped values: :25->12525, :587->12587,
  :465->12465. The raw daemon ports (25/587/465) are not directly reachable
  from the host; conftest.py SMTP_PORT/SUBMISSION_PORT/SMTPS_PORT carry the
  correct values.
- The compose stack has POSTSCREEN_ENABLED=false and GREYLISTING_ENABLED=false,
  so the greylist test is marked @pytest.mark.optional and skipped when the
  flag is off.
"""
import subprocess

import pytest

from conftest import MAIL_HOST, SMTP_PORT, SUBMISSION_PORT, SMTPS_PORT


def _ehlo_capabilities(port: int) -> str:
    """Return the raw EHLO response from swaks --quit-after EHLO on a port."""
    out = subprocess.run(
        [
            "swaks", "--server", f"{MAIL_HOST}:{port}",
            "--ehlo", "test.example.test",
            "--quit-after", "EHLO",
        ],
        capture_output=True, text=True,
    )
    return out.stdout + out.stderr


@pytest.mark.integration
def test_port25_offers_no_auth(compose):
    caps = _ehlo_capabilities(SMTP_PORT)
    assert "250-" in caps  # got an EHLO response at all
    assert "AUTH" not in caps.upper().replace("AUTHENTICATION", ""), \
        "port 25 must not advertise SASL AUTH"


@pytest.mark.integration
def test_submission_587_offers_auth_after_starttls(compose):
    # 587 advertises STARTTLS; AUTH appears only after the TLS upgrade
    # (smtpd_tls_auth_only=yes). swaks --tls performs STARTTLS then EHLO again.
    out = subprocess.run(
        [
            "swaks", "--server", f"{MAIL_HOST}:{SUBMISSION_PORT}",
            "--ehlo", "test.example.test",
            "--tls", "--quit-after", "EHLO",
        ],
        capture_output=True, text=True,
    )
    caps = out.stdout + out.stderr
    assert "AUTH" in caps.upper(), "submission must advertise AUTH after STARTTLS"


@pytest.mark.integration
def test_smtps_465_offers_auth(compose):
    out = subprocess.run(
        [
            "swaks", "--server", f"{MAIL_HOST}:{SMTPS_PORT}",
            "--ehlo", "test.example.test",
            "--tlsc", "--quit-after", "EHLO",
        ],
        capture_output=True, text=True,
    )
    caps = out.stdout + out.stderr
    assert "AUTH" in caps.upper(), "smtps must advertise AUTH"


@pytest.mark.integration
@pytest.mark.optional
def test_greylist_soft_reject_first_unauth_inbound(compose):
    """First unauthenticated inbound delivery to a local mailbox is soft-rejected
    (451) when greylisting is on. Marked optional: depends on Rspamd reaching the
    greylist action, which needs the seeded clean-but-greylistable score.
    The compose stack has GREYLISTING_ENABLED=false by default; run with
    GREYLISTING_ENABLED=true to exercise this path.
    """
    out = subprocess.run(
        [
            "swaks", "--server", f"{MAIL_HOST}:{SMTP_PORT}",
            "--from", "stranger@external.test",
            "--to", "alice@example.test",
            "--ehlo", "stranger.external.test",
            "--header", "Subject: greylist probe",
            "--body", "probe",
        ],
        capture_output=True, text=True,
    )
    resp = out.stdout + out.stderr
    assert "451" in resp, "first unauth inbound should be greylisted (451)"
