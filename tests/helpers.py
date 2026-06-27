"""Shared helpers for mail-server integration tests (send-as / sender_login_maps).

The integration stack (compose.test.yml, seed.sql) is owned by phase A. These
helpers only *drive* it: run swaks against the running mail-server container and
read back SMTP reply codes + Postfix log lines.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

# --- compose location (phase A) -------------------------------------------------
COMPOSE_FILE = os.environ.get(
    "MAIL_COMPOSE_FILE",
    os.path.join(os.path.dirname(__file__), "compose.test.yml"),
)
# The compose file declares `name: mail-server-itest`; do not override with -p.
MAIL_SERVICE = "mail-server"

# --- seeded credentials (phase A seed.sql) --------------------------------------
# Plaintext passwords whose ARGON2ID hashes phase A bakes into seed.sql.
# Both alice@ and bob@ use 'secret' (see tests/seed.sql).
ALICE = "alice@example.test"
ALICE_PW = os.environ.get("SEED_ALICE_PW", "secret")
BOB = "bob@example.test"
BOB_PW = os.environ.get("SEED_BOB_PW", "secret")
EXT_RCPT = "sink@external.test"  # delivered to the catch-all sink, not local

# Host-mapped ports (from compose.test.yml port bindings)
SUBMISSION_PORT = int(os.environ.get("MAIL_SUBMISSION_PORT", "12587"))  # 587 in-container
SMTPS_PORT = int(os.environ.get("MAIL_SMTPS_PORT", "12465"))            # 465 in-container
MAIL_HOST = os.environ.get("MAIL_HOST", "127.0.0.1")


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", "-f", COMPOSE_FILE, *args]


@dataclass
class SwaksResult:
    code: int           # swaks process exit code
    transcript: str     # full client/server transcript (stdout+stderr)

    def reply_for(self, smtp_verb: str) -> str | None:
        """Return the server reply line that answered the given client verb.

        swaks uses '->' / '<-' before TLS and '~>' / '<~' after STARTTLS.
        Error responses are marked '<~*' or '<-*'. We handle all variants.
        """
        lines = self.transcript.splitlines()
        seen = False
        for ln in lines:
            stripped = ln.lstrip()
            # Client line: starts with -> or ~> (possibly with leading spaces)
            is_client = stripped.startswith("->") or stripped.startswith("~>")
            # Server line: starts with <- or <~ (with optional * for error)
            is_server = stripped.startswith("<-") or stripped.startswith("<~")
            if not seen and is_client and smtp_verb in ln:
                seen = True
                continue
            if seen and is_server:
                # Strip the prefix (<-, <~, <-*, <~*) to get the reply text
                reply = re.sub(r"^<[-~]\*?\s*", "", stripped)
                return reply.strip()
        return None

    def reply_code(self, smtp_verb: str) -> int | None:
        line = self.reply_for(smtp_verb)
        if line is None:
            return None
        m = re.match(r"(\d{3})", line)
        return int(m.group(1)) if m else None


def run_swaks(
    *,
    auth_user: str | None,
    auth_pass: str | None,
    mail_from: str,
    rcpt_to: str = EXT_RCPT,
    port: int = SUBMISSION_PORT,
    tls: str = "starttls",  # 'starttls' | 'wrap' | 'none'
    timeout: int = 30,
) -> SwaksResult:
    """Run swaks on the host against the published submission port."""
    exe = shutil.which("swaks") or os.path.expanduser("~/.local/bin/swaks")
    if not os.path.isfile(exe):
        raise RuntimeError("swaks not found; expected at ~/.local/bin/swaks")
    cmd = [
        exe,
        "--server", MAIL_HOST,
        "--port", str(port),
        "--ehlo", "tester.example.test",
        "--from", mail_from,
        "--to", rcpt_to,
        "--timeout", str(timeout),
        "--hide-informational",
    ]
    if tls == "starttls":
        cmd += ["--tls"]
    elif tls == "wrap":
        cmd += ["--tlsc"]
    # self-signed cert in tests: do not verify the peer
    if tls in ("starttls", "wrap"):
        cmd += ["--tls-on-connect"] if tls == "wrap" else []
    if auth_user is not None:
        cmd += ["--auth", "LOGIN", "--auth-user", auth_user,
                "--auth-password", auth_pass or ""]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    return SwaksResult(code=proc.returncode, transcript=proc.stdout + proc.stderr)


def mail_log(grep: str | None = None, lines: int = 400) -> str:
    """Return recent Postfix log output from the mail-server container."""
    proc = subprocess.run(
        _compose("logs", "--no-color", "--tail", str(lines), MAIL_SERVICE),
        capture_output=True, text=True, timeout=30,
    )
    out = proc.stdout + proc.stderr
    if grep:
        out = "\n".join(l for l in out.splitlines() if re.search(grep, l))
    return out


def wait_for_smtp(port: int = SUBMISSION_PORT, timeout: int = 120) -> None:
    """Block until the submission listener answers with a 220 banner."""
    import socket
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with socket.create_connection((MAIL_HOST, port), timeout=5) as s:
                banner = s.recv(512).decode(errors="replace")
                if "220" in banner:
                    return
                last = banner
        except OSError as e:
            last = str(e)
        time.sleep(2)
    raise TimeoutError(f"submission port {port} never returned 220:\n{last}")


def have_compose() -> bool:
    return shutil.which("docker") is not None and os.path.exists(COMPOSE_FILE)
