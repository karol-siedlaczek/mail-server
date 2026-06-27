#!/usr/bin/env python3
"""audit-svc: populate audit_logs from two sources.

1. Dovecot auth-policy HTTP endpoint on 127.0.0.1:4001 — every IMAP/POP3/
   submission/smtps authentication (success AND failure) is reported here as
   JSON; one event_type='auth' row is written.
2. Postfix maillog correlator — Postfix log lines are read from stdin (the
   postfix s6 run script tees its log into a FIFO we read), grouped by
   queue-id, and emitted as event_type='send' (outbound) or 'delivery'
   (inbound LMTP) rows when the queue-id is 'removed'.

All INSERT SQL is loaded from operator-editable files under SQL_DIR, never
hardcoded. Uses the mail-server-audit Postgres role (PG_AUDIT_USER/PG_AUDIT_PASSWORD).

Pure, unit-tested functions: parse_auth_report, parse_maillog_line, and the
Correlator.flush_queue / ingest methods (no DB, no sockets).
"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SQL_DIR = os.environ.get("AUDIT_SQL_DIR", "/sql/audit")
LISTEN_HOST = os.environ.get("AUDIT_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AUDIT_LISTEN_PORT", "4001"))
MAIL_HOST = os.environ.get("MAIL_HOSTNAME", "")


def parse_auth_report(report):
    """Map a Dovecot auth-policy JSON report -> named params for auth_insert.sql."""
    login = report.get("username") or report.get("login")
    src_ip = report.get("remote") or None
    service = report.get("service") or report.get("protocol") or "?"
    success = bool(report.get("success"))
    note = "policy_reject" if report.get("policy_reject") else (
        "success" if success else "failure")
    msg = "auth %s via %s" % (note, service)
    return {
        "login": login,
        "src_ip": src_ip,
        "success": success,
        "host": MAIL_HOST or None,
        "msg": msg,
    }


# Postfix lines: <prefix> postfix/<daemon>[<pid>]: <QUEUEID>: <key=val, ...>
# daemon may be a plain name (qmgr, smtp, lmtp) OR a service-qualified
# smtpd (submission/smtpd, smtps/smtpd) when a service sets syslog_name — the
# authenticated submission line lives there, so it must match or login is lost.
_QID_RE = re.compile(r"postfix/(?P<daemon>[a-z]+(?:/[a-z]+)?)\[\d+\]:\s+(?P<qid>[0-9A-F]{6,}):\s+(?P<rest>.*)$")
_CLIENT_RE = re.compile(r"client=[^\[]*\[(?P<ip>[0-9a-fA-F:.]+)\]")
_SASL_RE = re.compile(r"sasl_username=(?P<u>\S+?)(?:,|\s|$)")
_MSGID_RE = re.compile(r"message-id=(?P<m><[^>]*>|\S+)")
_FROM_RE = re.compile(r"\bfrom=<(?P<f>[^>]*)>")
_TO_RE = re.compile(r"\bto=<(?P<t>[^>]*)>")
_STATUS_RE = re.compile(r"\bstatus=(?P<s>\w+)")


def parse_maillog_line(line):
    """Parse one Postfix log line.

    Returns (queue_id, fields) with any of: src_ip, login, message_id, sender,
    recipient, status, transport, removed. Returns (None, {}) for non-queue lines.
    """
    m = _QID_RE.search(line)
    if not m:
        return None, {}
    qid = m.group("qid")
    daemon = m.group("daemon")
    rest = m.group("rest")
    fields = {}

    if rest.strip() == "removed":
        fields["removed"] = True
        return qid, fields

    cm = _CLIENT_RE.search(rest)
    if cm:
        fields["src_ip"] = cm.group("ip")
    sm = _SASL_RE.search(rest)
    if sm:
        fields["login"] = sm.group("u").rstrip(",")
    mm = _MSGID_RE.search(rest)
    if mm:
        fields["message_id"] = mm.group("m")
    fm = _FROM_RE.search(rest)
    if fm:
        fields["sender"] = fm.group("f")
    if daemon in ("smtp", "lmtp", "virtual", "pipe", "local"):
        tm = _TO_RE.search(rest)
        if tm:
            fields["recipient"] = tm.group("t")
        stm = _STATUS_RE.search(rest)
        if stm:
            fields["status"] = stm.group("s")
        fields["transport"] = daemon
    return qid, fields


class Correlator:
    """Accumulate per-queue-id facts; emit an event on 'removed'."""

    def __init__(self, host=""):
        self.host = host
        self.queues = {}

    def ingest(self, line):
        """Feed one log line. Returns a list of (kind, params) events (possibly
        empty) ready to INSERT, or None if the line carried no queue-id."""
        qid, fields = parse_maillog_line(line)
        if qid is None:
            return None
        q = self.queues.setdefault(qid, {})
        if fields.get("removed"):
            event = self.flush_queue(qid)
            return [event] if event else []
        for k, v in fields.items():
            q[k] = v
        return None

    def flush_queue(self, qid):
        """Build one (kind, params) tuple for a finished queue-id, then forget
        it. Returns None for an unknown queue-id."""
        q = self.queues.pop(qid, None)
        if q is None:
            return None
        transport = q.get("transport")
        if transport == "lmtp":
            params = {
                "host": self.host or None,
                "sender": q.get("sender") or None,
                "recipient": q.get("recipient") or None,
                "message_id": q.get("message_id") or None,
                "queue_id": qid,
                "msg": q.get("status") or None,
            }
            return ("delivery", params)
        params = {
            "login": q.get("login") or None,
            "src_ip": q.get("src_ip") or None,
            "host": self.host or None,
            "sender": q.get("sender") or None,
            "recipient": q.get("recipient") or None,
            "message_id": q.get("message_id") or None,
            "queue_id": qid,
            "score": None,
            "msg": q.get("status") or None,
        }
        return ("send", params)


def _load_sql():
    sql = {}
    for name, fn in (("auth", "auth_insert.sql"),
                     ("send", "send_insert.sql"),
                     ("delivery", "delivery_insert.sql")):
        with open(os.path.join(SQL_DIR, fn), "r", encoding="utf-8") as fh:
            sql[name] = fh.read()
    return sql


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("PG_HOST"),
        port=os.environ.get("PG_PORT", "5432"),
        dbname=os.environ.get("PG_DBNAME"),
        user=os.environ.get("PG_AUDIT_USER") or os.environ.get("PG_USER"),
        password=os.environ.get("PG_AUDIT_PASSWORD") or os.environ.get("PG_PASSWORD"),
    )


class _Writer:
    """Serialise INSERTs; reconnect on failure so a DB blip never kills audit."""

    def __init__(self, sql):
        self.sql = sql
        self.lock = threading.Lock()
        self.conn = None

    def _conn(self):
        if self.conn is None or self.conn.closed:
            self.conn = _connect()
            self.conn.autocommit = True
        return self.conn

    def write(self, kind, params):
        with self.lock:
            try:
                with self._conn().cursor() as cur:
                    cur.execute(self.sql[kind], params)
            except Exception as exc:  # noqa: BLE001 - audit must not crash mail
                self.conn = None
                sys.stderr.write("audit-svc: insert failed (%s): %s\n" % (kind, exc))


def _make_handler(writer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            qs = parse_qs(urlparse(self.path).query)
            command = (qs.get("command") or [""])[0]
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"result":true}')
            if command == "report":
                try:
                    report = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    return
                writer.write("auth", parse_auth_report(report))
    return Handler


def _tail_log(writer):
    correlator = Correlator(host=MAIL_HOST)
    for line in sys.stdin:
        events = correlator.ingest(line.rstrip("\n"))
        if not events:
            continue
        for kind, params in events:
            writer.write(kind, params)


def main():
    sql = _load_sql()
    writer = _Writer(sql)
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), _make_handler(writer))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    sys.stderr.write("audit-svc: listening on %s:%d, tailing maillog\n"
                     % (LISTEN_HOST, LISTEN_PORT))
    _tail_log(writer)


if __name__ == "__main__":
    main()
