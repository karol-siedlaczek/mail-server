"""Unit tests for the pure functions in audit-svc.py.

These exercise parsing only — no DB, no sockets. The module is loaded by path
because it lives under rootfs/ and has a hyphen in its name.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SVC = os.path.normpath(
    os.path.join(_HERE, "..", "rootfs", "usr", "local", "bin", "audit-svc.py")
)
_spec = importlib.util.spec_from_file_location("audit_svc", _SVC)
audit_svc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_svc)


# parse_auth_report
def test_parse_auth_report_success():
    report = {"username": "alice@example.test", "remote": "203.0.113.7",
              "service": "imap", "success": True}
    row = audit_svc.parse_auth_report(report)
    assert row["login"] == "alice@example.test"
    assert row["src_ip"] == "203.0.113.7"
    assert row["success"] is True
    assert "imap" in row["msg"]


def test_parse_auth_report_failure():
    report = {"username": "alice@example.test", "remote": "203.0.113.7",
              "service": "submission", "success": False, "policy_reject": False}
    row = audit_svc.parse_auth_report(report)
    assert row["login"] == "alice@example.test"
    assert row["src_ip"] == "203.0.113.7"
    assert row["success"] is False


def test_parse_auth_report_login_alias_and_missing_ip():
    report = {"login": "bob@example.test", "service": "pop3", "success": True}
    row = audit_svc.parse_auth_report(report)
    assert row["login"] == "bob@example.test"
    assert row["src_ip"] is None


# parse_maillog_line
def test_parse_maillog_smtpd_client_and_sasl():
    line = ("Jun 15 10:00:01 mail postfix/smtpd[123]: ABC123: "
            "client=host.example[203.0.113.7], sasl_method=PLAIN, "
            "sasl_username=bob@example.test")
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "ABC123"
    assert fields["src_ip"] == "203.0.113.7"
    assert fields["login"] == "bob@example.test"


def test_parse_maillog_cleanup_message_id():
    line = ("Jun 15 10:00:01 mail postfix/cleanup[124]: ABC123: "
            "message-id=<deadbeef@example.test>")
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "ABC123"
    assert fields["message_id"] == "<deadbeef@example.test>"


def test_parse_maillog_qmgr_from():
    line = ("Jun 15 10:00:02 mail postfix/qmgr[125]: ABC123: "
            "from=<alice@example.test>, size=1234, nrcpt=1 (queue active)")
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "ABC123"
    assert fields["sender"] == "alice@example.test"


def test_parse_maillog_smtp_delivery_recipient():
    line = ("Jun 15 10:00:03 mail postfix/smtp[126]: ABC123: "
            "to=<dest@remote.test>, relay=remote.test[198.51.100.1]:25, "
            "delay=1, status=sent (250 OK)")
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "ABC123"
    assert fields["recipient"] == "dest@remote.test"
    assert fields["status"] == "sent"
    assert fields["transport"] == "smtp"


def test_parse_maillog_lmtp_delivery():
    line = ("Jun 15 10:00:03 mail postfix/lmtp[127]: DEF456: "
            "to=<alice@example.test>, relay=mail.example.test[private/dovecot-lmtp], "
            "delay=0.5, status=sent (250 2.0.0 delivered)")
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "DEF456"
    assert fields["recipient"] == "alice@example.test"
    assert fields["transport"] == "lmtp"


def test_parse_maillog_removed():
    line = "Jun 15 10:00:04 mail postfix/qmgr[125]: ABC123: removed"
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid == "ABC123"
    assert fields["removed"] is True


def test_parse_maillog_non_queue_line():
    line = "Jun 15 10:00:00 mail postfix/master[1]: daemon started"
    qid, fields = audit_svc.parse_maillog_line(line)
    assert qid is None


# flush_queue (multi-line correlation)
def test_flush_queue_send_event():
    c = audit_svc.Correlator(host="mail.example.test")
    for line in [
        ("Jun 15 10:00:01 mail postfix/smtpd[123]: ABC123: client=h[203.0.113.7], "
         "sasl_method=PLAIN, sasl_username=bob@example.test"),
        "Jun 15 10:00:01 mail postfix/cleanup[124]: ABC123: message-id=<mid@example.test>",
        ("Jun 15 10:00:02 mail postfix/qmgr[125]: ABC123: from=<alice@example.test>, "
         "size=1, nrcpt=1 (queue active)"),
        ("Jun 15 10:00:03 mail postfix/smtp[126]: ABC123: to=<dest@remote.test>, "
         "relay=r[198.51.100.1]:25, status=sent (250 OK)"),
    ]:
        assert c.ingest(line) is None
    events = c.ingest("Jun 15 10:00:04 mail postfix/qmgr[125]: ABC123: removed")
    assert len(events) == 1
    kind, params = events[0]
    assert kind == "send"
    assert params["login"] == "bob@example.test"
    assert params["src_ip"] == "203.0.113.7"
    assert params["sender"] == "alice@example.test"
    assert params["message_id"] == "<mid@example.test>"
    assert params["recipient"] == "dest@remote.test"
    assert params["queue_id"] == "ABC123"
    assert params["host"] == "mail.example.test"
    assert "ABC123" not in c.queues


def test_flush_queue_delivery_event_no_sasl():
    c = audit_svc.Correlator(host="mail.example.test")
    for line in [
        "Jun 15 10:00:00 mail postfix/smtpd[123]: DEF456: client=ext[198.51.100.9]",
        "Jun 15 10:00:00 mail postfix/cleanup[124]: DEF456: message-id=<in@remote.test>",
        ("Jun 15 10:00:01 mail postfix/qmgr[125]: DEF456: from=<sender@remote.test>, "
         "size=1, nrcpt=1 (queue active)"),
        ("Jun 15 10:00:02 mail postfix/lmtp[127]: DEF456: to=<alice@example.test>, "
         "relay=mail[private/dovecot-lmtp], status=sent (250 delivered)"),
    ]:
        c.ingest(line)
    events = c.ingest("Jun 15 10:00:03 mail postfix/qmgr[125]: DEF456: removed")
    assert len(events) == 1
    kind, params = events[0]
    assert kind == "delivery"
    assert params["recipient"] == "alice@example.test"
    assert params["sender"] == "sender@remote.test"
    assert params["queue_id"] == "DEF456"


def test_flush_queue_unknown_qid_returns_none():
    c = audit_svc.Correlator(host="mail.example.test")
    assert c.flush_queue("NOPE") is None
