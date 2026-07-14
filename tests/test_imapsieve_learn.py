# tests/test_imapsieve_learn.py
import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "rootfs" / "usr" / "lib" / "dovecot" / "sieve"

def _read(name):
    return (BIN / name).read_text()

def test_learn_spam_wrapper_calls_rspamc():
    assert (BIN / "rspamd-learn-spam.sh").is_file()
    t = _read("rspamd-learn-spam.sh")
    assert t.startswith("#!/bin/sh")
    assert "/usr/bin/rspamc" in t
    assert "learn_spam" in t
    # bounded wait + swallow expected non-zero exit (already-learned / down)
    assert "-t 10" in t
    assert "|| true" in t

def test_learn_ham_wrapper_calls_rspamc():
    assert (BIN / "rspamd-learn-ham.sh").is_file()
    t = _read("rspamd-learn-ham.sh")
    assert "/usr/bin/rspamc" in t
    assert "learn_ham" in t
    assert "|| true" in t

def test_wrappers_are_executable_in_git():
    for name in ("rspamd-learn-spam.sh", "rspamd-learn-ham.sh"):
        mode = os.stat(BIN / name).st_mode
        assert mode & stat.S_IXUSR, f"{name} not executable"

SIEVE = REPO / "rootfs" / "etc" / "dovecot" / "sieve"

def test_report_spam_sieve_pipes_to_wrapper():
    t = (SIEVE / "report-spam.sieve").read_text()
    assert 'require ["vnd.dovecot.pipe"];' in t
    assert 'pipe "rspamd-learn-spam.sh";' in t

def test_report_ham_sieve_pipes_to_wrapper():
    t = (SIEVE / "report-ham.sieve").read_text()
    assert 'require ["vnd.dovecot.pipe", "environment"];' in t
    assert 'pipe "rspamd-learn-ham.sh";' in t

def test_report_ham_skips_trash_to_avoid_bayes_poisoning():
    # Junk -> Trash ("delete spam") must NOT teach ham; guard on the destination.
    t = (SIEVE / "report-ham.sieve").read_text()
    assert 'environment :is "imap.mailbox" "Trash"' in t
    # the pipe must be inside the guard (after the `if not ... {`)
    assert t.index("if not environment") < t.index('pipe "rspamd-learn-ham.sh";')
