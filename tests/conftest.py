"""Shared fixtures and protocol helpers for mail-server tests.

Imported by every phase's test module. Helper names are part of the shared
test contract — do not rename or redefine them downstream.
"""
import imaplib
import json
import os
import shutil
import socket
import subprocess
import time

import pytest

# ── Connection coordinates (match tests/compose.test.yml host port mappings) ──
PG_DSN = {
    "host": os.environ.get("PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("PG_PORT", "5432")),
    "dbname": os.environ.get("PG_DBNAME", "maildb"),
    "user": os.environ.get("PG_USER", "maildba"),
    "password": os.environ.get("PG_PASSWORD", "testpw"),
}
SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "12525"))
SUBMISSION_PORT = int(os.environ.get("MAIL_SUBMISSION_PORT", "12587"))
SMTPS_PORT = int(os.environ.get("MAIL_SMTPS_PORT", "12465"))
IMAP_PORT = int(os.environ.get("MAIL_IMAP_PORT", "12143"))
IMAPS_PORT = int(os.environ.get("MAIL_IMAPS_PORT", "12993"))
MAIL_HOST = os.environ.get("MAIL_HOST", "127.0.0.1")

COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "compose.test.yml")


def pg_dsn():
    """Return the libpq DSN string for the test Postgres."""
    return " ".join(f"{k}={v}" for k, v in PG_DSN.items())


def pg_connect():
    """Open a psycopg2 connection to the test Postgres (autocommit)."""
    import psycopg2  # imported lazily so unit tests don't need the driver

    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    return conn


def wait_for_port(host, port, timeout=60.0):
    """Block until host:port accepts a TCP connection, or raise TimeoutError."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError as exc:  # noqa: PERF203
            last = exc
            time.sleep(0.5)
    raise TimeoutError(f"{host}:{port} not reachable in {timeout}s ({last})")


def imap_login(user, password, host=MAIL_HOST, port=None, ssl=True):
    """Log in over IMAP and return the connected imaplib client."""
    port = port or (IMAPS_PORT if ssl else IMAP_PORT)
    cls = imaplib.IMAP4_SSL if ssl else imaplib.IMAP4
    client = cls(host, port)
    if not ssl:
        client.starttls()
    client.login(user, password)
    return client


def swaks(to, mail_from, server=None, port=None, auth_user=None,
          auth_password=None, tls=False, tlsc=False, body=None,
          header=None, extra=None, check=True):
    """Drive an SMTP transaction via the `swaks` CLI; return CompletedProcess.

    server/port default to the inbound :25 mapping. Set auth_user to use
    submission; tls=STARTTLS, tlsc=implicit TLS.
    """
    exe = shutil.which("swaks")
    if exe is None:
        raise RuntimeError("swaks not installed (apt-get install swaks)")
    server = server or MAIL_HOST
    port = port or SMTP_PORT
    cmd = [exe, "--to", to, "--from", mail_from,
           "--server", f"{server}:{port}", "--timeout", "30"]
    if auth_user:
        cmd += ["--auth", "--auth-user", auth_user,
                "--auth-password", auth_password or ""]
    if tls:
        cmd += ["--tls"]
    if tlsc:
        cmd += ["--tlsc"]
    if body is not None:
        cmd += ["--body", body]
    for h in (header or []):
        cmd += ["--header", h]
    if extra:
        cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def read_sink():
    """Return the list of messages the catch-all sink recorded (JSON lines)."""
    out = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "sink",
         "cat", "/var/sink/messages.json"],
        capture_output=True, text=True,
    )
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


# ── Session fixture: bring the whole integration stack up/down ───────────────
@pytest.fixture(scope="session")
def compose():
    """Build + start the compose stack for integration tests, tear it down after.

    Skips cleanly when docker/compose is unavailable so unit-only runs still
    collect. Profiles can be added via COMPOSE_PROFILES in the environment.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    up = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "--build", "--wait"],
        capture_output=True, text=True,
    )
    if up.returncode != 0:
        pytest.skip(f"compose up failed:\n{up.stdout}\n{up.stderr}")
    try:
        wait_for_port(MAIL_HOST, IMAP_PORT, timeout=180)
        yield COMPOSE_FILE
    finally:
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "down", "-v"],
            capture_output=True, text=True,
        )


@pytest.fixture(scope="session")
def db(compose):
    """A live psycopg2 connection to the running test Postgres."""
    conn = pg_connect()
    yield conn
    conn.close()
