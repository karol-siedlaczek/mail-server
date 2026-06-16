"""Shared fixtures and protocol helpers for mail-server tests.

Imported by every phase's test module. Helper names are part of the shared
test contract — do not rename or redefine them downstream.

render-config.sh resolves env (secrets, defaults, validation) and renders
templates. To assert on the *resolved* values without booting the container,
we run the script with RENDER_DUMP_ENV=1, which makes it print the final
resolved variable set (KEY=VALUE, NUL-free) to stdout and exit 0 before any
filesystem writes that need root.
"""
import imaplib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

# ── Render-config helpers (D.1) ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent          # images/mail-server
RENDER = ROOT / "rootfs" / "usr" / "local" / "bin" / "render-config.sh"


def run_render(env=None, dump_env=True, render_root=None, expect_rc=0):
    """Run render-config.sh in an isolated environment.

    env:          dict of env vars to expose (the *only* vars set; PATH kept).
    dump_env:     set RENDER_DUMP_ENV=1 so the script prints resolved env and
                  exits before writing config (no root needed).
    render_root:  if set, RENDER_ROOT=<dir> so absolute dest paths are
                  rewritten under <dir> (used by D.2 to render into a tmpdir).
    Returns CompletedProcess; asserts the return code matches expect_rc.
    """
    base = {"PATH": os.environ["PATH"]}
    if env:
        base.update(env)
    if dump_env:
        base["RENDER_DUMP_ENV"] = "1"
    if render_root is not None:
        base["RENDER_ROOT"] = str(render_root)
    proc = subprocess.run(
        ["bash", str(RENDER)],
        env=base,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == expect_rc, (
        f"rc={proc.returncode} (wanted {expect_rc})\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc


def parse_dump(stdout):
    """Parse RENDER_DUMP_ENV output (lines 'KEY=VALUE') into a dict."""
    out = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


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


# ── B.x schema-unit fixtures (no compose; use PGTEST_DSN env var) ─────────────
import pathlib as _pathlib

_SQL_DIR = _pathlib.Path(__file__).resolve().parents[1] / "sql"
_SCHEMA_SQL = _SQL_DIR / "schema.sql"


def _pgtest_dsn():
    """Return the DSN for the throwaway Postgres used by schema unit tests."""
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        pytest.skip("PGTEST_DSN not set; start postgres and export PGTEST_DSN")
    return dsn


@pytest.fixture(scope="session")
def schema_sql_text():
    return _SCHEMA_SQL.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def db_loaded(schema_sql_text):
    """Load sql/schema.sql once per session (idempotent), yield a DSN.

    Runs the file twice to prove idempotency: a second apply must not error.
    """
    import psycopg2 as _psycopg2

    conn = _psycopg2.connect(_pgtest_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql_text)
            cur.execute(schema_sql_text)  # second apply: idempotency check
    finally:
        conn.close()
    return _pgtest_dsn()


@pytest.fixture()
def conn(db_loaded):
    """A transactional connection; every test's writes are rolled back."""
    import psycopg2 as _psycopg2

    c = _psycopg2.connect(db_loaded)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


# ── Dovecot render fixtures (phase F) ────────────────────────────────────────

REPO_TPL = Path(__file__).resolve().parents[1] / "rootfs" / "tpl" / "dovecot"
RENDER_SH = Path(__file__).resolve().parents[1] / "rootfs" / "usr" / "local" / "bin" / "render-config.sh"

# Env that exercises every conditional knob the dovecot templates read.
DOVECOT_RENDER_ENV = {
    "MAIL_HOSTNAME": "mail.example.test",
    "PG_HOST": "db",
    "PG_PORT": "5432",
    "PG_DBNAME": "mail",
    "PG_USER": "mail_ro",
    "PG_PASSWORD": "secret",
    "TLS_CERT_FILE": "/tls/fullchain.pem",
    "TLS_KEY_FILE": "/tls/privkey.pem",
    "PASSWORD_SCHEME": "ARGON2ID",
    "ALLOW_WEAK_SCHEMES": "false",
    "POP3_ENABLED": "false",
    # Precomputed-by-render values these templates expand. render-config.sh
    # derives them from the knobs above; we pass explicit fallbacks so a
    # plain `envsubst` over a single template still resolves every var.
    "DOVECOT_PASSWORD_SCHEME": "ARGON2ID",
    "DOVECOT_AUTH_ALLOW_WEAK": "no",
    "DOVECOT_POP3_PROTOCOLS": "",
    "DOVECOT_POP3_SERVICES": "",
}


def _render_one(tpl_name: str, env_overrides: dict, out_dir: Path) -> str:
    """Expand a single dovecot template with envsubst and return its text."""
    env = dict(DOVECOT_RENDER_ENV)
    env.update(env_overrides)
    src = REPO_TPL / tpl_name
    dst = out_dir / tpl_name.replace(".tpl", "")
    # Only the variables we set are substituted; literal $name not in env is
    # left intact (envsubst with an explicit SHELL-FORMAT var list).
    varlist = "".join("${%s}" % k for k in env)
    with open(src) as fh:
        rendered = subprocess.run(
            ["envsubst", varlist],
            stdin=fh,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    dst.write_text(rendered)
    return rendered


@pytest.fixture()
def render_dovecot(tmp_path):
    """Return a callable: render_dovecot('10-auth.conf.tpl', {overrides}) -> text."""
    def _do(tpl_name, env_overrides=None):
        return _render_one(tpl_name, env_overrides or {}, tmp_path)
    return _do
