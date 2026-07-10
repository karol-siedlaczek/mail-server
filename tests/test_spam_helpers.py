"""mail-learn-spam / mail-learn-ham / mail-release helpers.

Hermetic: stubs `rspamc` and `sendmail` on PATH so the tests assert the helpers
invoke the right subcommands (learn_spam/fuzzy_add/learn_ham, direct sendmail)
without a running rspamd or MTA.
"""
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "rootfs" / "usr" / "local" / "bin"


def _stub(bindir: Path, name: str, body: str):
    p = bindir / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run(helper, tmp_path, *args, stdin=None, extra_env=None):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    # Both stubs consume stdin (so the helper's pipes/redirects behave) and record
    # their argv, one line per call.
    _stub(bindir, "rspamc", f'cat >/dev/null; echo "rspamc $*" >> "{log}"\n')
    _stub(bindir, "sendmail", f'cat >/dev/null; echo "sendmail $*" >> "{log}"\n')
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["bash", str(BIN / helper), *args],
                       input=stdin, capture_output=True, text=True, env=env)
    return r, (log.read_text() if log.exists() else "")


def test_learn_spam_learns_bayes_and_fuzzy_adds(tmp_path):
    r, calls = run("mail-learn-spam", tmp_path, stdin="From: x\n\nspam body\n")
    assert r.returncode == 0, r.stderr
    assert "learn_spam" in calls
    assert "fuzzy_add" in calls
    assert "-f 1" in calls   # stored under the spam/deny flag


def test_learn_spam_passes_controller_password(tmp_path):
    r, calls = run("mail-learn-spam", tmp_path, stdin="m\n",
                   extra_env={"RSPAMD_CONTROLLER_PASSWORD": "s3cret"})
    assert r.returncode == 0, r.stderr
    assert "-P s3cret" in calls


def test_learn_ham_learns_ham_only(tmp_path):
    r, calls = run("mail-learn-ham", tmp_path, stdin="From: x\n\nham\n")
    assert r.returncode == 0, r.stderr
    assert "learn_ham" in calls
    assert "learn_spam" not in calls
    assert "fuzzy_add" not in calls


def test_release_requires_destination(tmp_path):
    r, _ = run("mail-release", tmp_path, stdin="msg\n")
    assert r.returncode != 0
    assert "usage" in (r.stderr + r.stdout).lower()


def test_release_sends_direct_and_learns_ham(tmp_path):
    r, calls = run("mail-release", tmp_path, "karol@gmail.com",
                   stdin="From: x\n\nbody\n",
                   extra_env={"MAIL_HOSTNAME": "mail.example.test"})
    assert r.returncode == 0, r.stderr
    # Re-injected straight to the destination (bypasses the recipient's Sieve).
    assert "sendmail" in calls and "karol@gmail.com" in calls
    # False positive → corrected in Bayes.
    assert "learn_ham" in calls
