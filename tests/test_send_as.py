"""Phase I: end-to-end sender_login_maps / send-as enforcement via swaks.

Scenarios (spec "Sender login maps / send-as"):
  1. alice AUTH, MAIL FROM alice            -> accepted (2xx)
  2. alice AUTH, MAIL FROM bob (no grant)   -> rejected 553 reject_sender_login_mismatch
  3. bob   AUTH, MAIL FROM alice (granted)  -> accepted (2xx)  [seed: bob may send as alice]
  4. no AUTH on 587, MAIL FROM alice        -> rejected (relay denied)

The grant row (login_email=bob@example.test, allowed_sender=alice@example.test)
and users alice/bob are seeded by phase A's tests/seed.sql.
"""
import pytest

from helpers import (
    ALICE, ALICE_PW, BOB, BOB_PW,
    SUBMISSION_PORT, have_compose, mail_log, run_swaks, wait_for_smtp,
)

pytestmark = pytest.mark.integration

if not have_compose():
    pytest.skip("integration stack (compose.test.yml) not available",
                allow_module_level=True)


@pytest.fixture(scope="module", autouse=True)
def _ready(compose):
    wait_for_smtp(SUBMISSION_PORT)


def _accepted(res):
    # swaks exits 0 and the DATA stage gets a 250 when the message is queued.
    assert res.code == 0, res.transcript
    assert res.reply_code(".") == 250 or res.reply_code("DATA") in (250, 354), res.transcript


def test_send_as_self_accepted():
    """1. alice authenticated, MAIL FROM alice -> accepted."""
    res = run_swaks(auth_user=ALICE, auth_pass=ALICE_PW, mail_from=ALICE)
    _accepted(res)
    # the submission is recorded with alice as the sasl_username
    assert "sasl_username=alice@example.test" in mail_log(grep="sasl_username"), \
        mail_log(grep="sasl_username")


def test_send_as_mismatch_rejected():
    """2. alice authenticated, MAIL FROM bob with NO grant -> 553 mismatch.

    With smtpd_delay_reject=yes (Postfix default), restrictions including
    reject_sender_login_mismatch are deferred and reported at RCPT TO.
    """
    res = run_swaks(auth_user=ALICE, auth_pass=ALICE_PW, mail_from=BOB)
    # reject_sender_login_mismatch fires at RCPT TO when smtpd_delay_reject=yes.
    assert res.reply_code("RCPT TO") == 553, res.transcript
    assert "Sender address rejected: not owned by user alice@example.test" in \
        (res.reply_for("RCPT TO") or ""), res.transcript
    log = mail_log(grep="reject_sender_login_mismatch|not owned by user")
    assert "not owned by user alice@example.test" in log, log


def test_send_as_granted_accepted():
    """3. bob authenticated, MAIL FROM alice WITH seeded grant -> accepted."""
    res = run_swaks(auth_user=BOB, auth_pass=BOB_PW, mail_from=ALICE)
    _accepted(res)
    # bob is the authenticated login even though the envelope sender is alice.
    assert "sasl_username=bob@example.test" in mail_log(grep="sasl_username"), \
        mail_log(grep="sasl_username")


def test_unauthenticated_submission_rejected():
    """4. unauthenticated on 587 -> rejected (554/530).

    With smtpd_client_restrictions=permit_sasl_authenticated,reject on submission,
    unauthenticated clients are rejected at RCPT TO (smtpd_delay_reject=yes).
    The rejection may appear as 'Client host rejected', 'Relay access denied',
    or 'Authentication required' depending on which restriction fires first.
    """
    res = run_swaks(auth_user=None, auth_pass=None, mail_from=ALICE)
    # smtpd_client_restrictions / smtpd_relay_restrictions reject -> 554 or 530.
    code = res.reply_code("RCPT TO") or res.reply_code("MAIL FROM")
    assert code in (530, 554), res.transcript
    log = mail_log(grep="Relay access denied|Authentication required|Client host rejected|Access denied")
    assert any(phrase in log for phrase in [
        "Relay access denied", "Authentication required",
        "Client host rejected", "Access denied",
    ]), log
