"""Integration: mail-bootstrap seeds an empty DB once and is a no-op on rerun.

Requires the built image (`mail-server:test`) and Docker. Skipped if either is
absent so `make test` (unit-only) stays green; `make itest` runs it for real.
"""
import os
import shutil
import subprocess
import time
import uuid

import pytest

IMAGE = os.environ.get("MAIL_IMAGE", "mail-server:test")
PG_IMAGE = "postgres:16"
DOMAIN = "boot.test"
ADMIN = f"admin@{DOMAIN}"
PASSWORD = "bootstrap-secret"


def _docker(*args, **kw):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)


def _have_docker():
    if not shutil.which("docker"):
        return False
    return _docker("image", "inspect", IMAGE).returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _have_docker(),
                       reason="docker or built image unavailable"),
]


@pytest.fixture()
def pg_and_net():
    net = f"bootnet-{uuid.uuid4().hex[:8]}"
    pg = f"bootpg-{uuid.uuid4().hex[:8]}"
    _docker("network", "create", net, check=True)
    try:
        r = _docker("run", "-d", "--name", pg, "--network", net,
                    "-e", "POSTGRES_DB=mail",
                    "-e", "POSTGRES_USER=mail",
                    "-e", "POSTGRES_PASSWORD=mailpw",
                    PG_IMAGE)
        assert r.returncode == 0, r.stderr
        # Wait for readiness. pg_isready goes green during the image's init
        # phase, BEFORE POSTGRES_DB ('mail') is created — under heavy load the
        # schema apply then races ahead and hits "database mail does not exist".
        # Poll an actual query against the target DB instead.
        for _ in range(60):
            if _docker("exec", pg, "psql", "-U", "mail", "-d", "mail",
                       "-c", "SELECT 1").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres never became ready")
        # Apply the schema shipped by phase B.
        schema = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")
        with open(schema, "rb") as fh:
            r = subprocess.run(
                ["docker", "exec", "-i", pg, "psql", "-U", "mail", "-d", "mail",
                 "-v", "ON_ERROR_STOP=1"],
                stdin=fh, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        yield net, pg
    finally:
        _docker("rm", "-f", pg)
        _docker("network", "rm", net)


def _run_bootstrap(net, pg, vol):
    """Run only the mail-bootstrap script (not the full s6 boot)."""
    return _docker(
        "run", "--rm", "--network", net,
        "-v", f"{vol}:/var/lib/rspamd",
        "-e", "MAIL_HOSTNAME=mail.boot.test",
        "-e", f"PG_HOST={pg}", "-e", "PG_DBNAME=mail",
        "-e", "PG_USER=mail", "-e", "PG_PASSWORD=mailpw",
        "-e", f"MAIL_BOOTSTRAP_DOMAIN={DOMAIN}",
        "-e", f"MAIL_BOOTSTRAP_ADMIN={ADMIN}",
        "-e", f"MAIL_BOOTSTRAP_PASSWORD={PASSWORD}",
        "--entrypoint", "/usr/local/bin/mail-bootstrap",
        IMAGE)


def _query(pg, sql):
    r = _docker("exec", pg, "psql", "-U", "mail", "-d", "mail",
                "-tAc", sql)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_bootstrap_seeds_then_is_idempotent(pg_and_net):
    net, pg = pg_and_net
    vol = f"bootvol-{uuid.uuid4().hex[:8]}"
    _docker("volume", "create", vol, check=True)
    try:
        # --- first run: empty domains table -> seed ---
        assert _query(pg, "SELECT count(*) FROM domains") == "0"
        r1 = _run_bootstrap(net, pg, vol)
        assert r1.returncode == 0, r1.stderr + r1.stdout

        assert _query(pg, "SELECT count(*) FROM domains") == "1"
        assert _query(pg, f"SELECT domain FROM domains") == DOMAIN
        assert _query(pg, "SELECT dkim_selector FROM domains") == "default"
        assert _query(pg, "SELECT count(*) FROM users") == "1"
        assert _query(pg, "SELECT email FROM users") == ADMIN
        # Hash is scheme-prefixed and loginable (ARGON2ID by default).
        pwd = _query(pg, "SELECT password FROM users")
        assert pwd.startswith("{ARGON2ID}") or pwd.startswith("$argon2id$"), pwd
        # DKIM key landed on the volume.
        ls = _docker("run", "--rm", "--entrypoint", "ls",
                     "-v", f"{vol}:/k", IMAGE, "/k/dkim")
        assert f"{DOMAIN}.default.key" in ls.stdout, ls.stdout + ls.stderr
        key_mtime = _docker("run", "--rm", "--entrypoint", "stat",
                            "-v", f"{vol}:/k", IMAGE,
                            "-c", "%Y",
                            f"/k/dkim/{DOMAIN}.default.key").stdout.strip()
        # The DNS block is printed.
        assert "DNS records" in r1.stdout

        # --- second run: domains non-empty -> strict no-op ---
        r2 = _run_bootstrap(net, pg, vol)
        assert r2.returncode == 0, r2.stderr + r2.stdout
        assert "no-op" in r2.stdout
        assert _query(pg, "SELECT count(*) FROM domains") == "1"
        assert _query(pg, "SELECT count(*) FROM users") == "1"
        # Key is reused, not regenerated.
        key_mtime2 = _docker("run", "--rm", "--entrypoint", "stat",
                             "-v", f"{vol}:/k", IMAGE,
                             "-c", "%Y",
                             f"/k/dkim/{DOMAIN}.default.key").stdout.strip()
        assert key_mtime2 == key_mtime, "DKIM key was regenerated on rerun"
    finally:
        _docker("volume", "rm", "-f", vol)
