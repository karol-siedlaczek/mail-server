# tests/test_sieve_forward.py
import importlib.util
import importlib.machinery
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MOD_PATH = REPO / "rootfs" / "usr" / "local" / "bin" / "sieve-forward-sync"

def _load():
    loader = importlib.machinery.SourceFileLoader("sfs", str(MOD_PATH))
    spec = importlib.util.spec_from_loader("sfs", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_build_sieve_no_copy_forward():
    s = _load().build_sieve([("karol@siedlaczek.com.pl", "karol@gmail.com", False)])
    assert 'require ["envelope"' in s
    assert 'if envelope :is "to" "karol@siedlaczek.com.pl"' in s
    assert 'redirect "karol@gmail.com";' in s
    assert ":copy" not in s
    # no-keep ham path ends in stop (spam branch + ham branch) → two stops.
    assert s.count("stop;") == 2

def test_build_sieve_spam_filed_to_junk_not_forwarded():
    # Spam (rspamd added X-Spam: Yes) must be filed to the local Junk mailbox for
    # review and NEVER forwarded — a `stop` before the redirect guarantees it.
    s = _load().build_sieve([("karol@siedlaczek.com.pl", "karol@gmail.com", False)])
    assert '"fileinto"' in s                       # extension required
    assert 'if header :contains "X-Spam" "Yes"' in s
    assert 'fileinto "Junk";' in s
    junk_idx = s.index('fileinto "Junk";')
    redir_idx = s.index('redirect "karol@gmail.com";')
    assert junk_idx < redir_idx                    # Junk-filing comes first
    assert s.index("stop;") < redir_idx            # and stops before the redirect

def test_build_sieve_spam_junk_applies_even_with_keep_copy():
    # Even a keep_copy forward must stop after filing spam to Junk, else spam
    # would still be relayed by the redirect below.
    s = _load().build_sieve([("a@ex.pl", "a@gmail.com", True)])
    assert 'fileinto "Junk";' in s
    assert s.count("stop;") == 1                    # only the spam-branch stop

def test_build_sieve_keep_copy_uses_copy_and_no_ham_stop():
    s = _load().build_sieve([("a@ex.pl", "a@gmail.com", True)])
    assert 'redirect :copy "a@gmail.com";' in s
    # keep_copy → the redirect must not be followed by a stop (implicit keep
    # survives). The only stop is in the spam branch.
    assert s.count("stop;") == 1

def test_build_sieve_multi_destination():
    s = _load().build_sieve([
        ("a@ex.pl", "one@gmail.com", False),
        ("a@ex.pl", "two@out.com", False),
    ])
    assert 'redirect "one@gmail.com";' in s
    assert 'redirect "two@out.com";' in s
    # single guarded block per source
    assert s.count('if envelope :is "to" "a@ex.pl"') == 1

def test_build_sieve_escapes_quotes():
    s = _load().build_sieve([('x"y@ex.pl', 'd@gmail.com', False)])
    assert '\\"' in s

def test_build_sieve_mixed_keep_copy_promotes_all_to_copy():
    # If ANY row for a source keeps a copy, every destination for that source
    # uses :copy and the block must NOT emit `stop` (implicit keep survives).
    s = _load().build_sieve([
        ("a@ex.pl", "one@gmail.com", False),
        ("a@ex.pl", "two@out.com", True),
    ])
    assert 'redirect :copy "one@gmail.com";' in s
    assert 'redirect :copy "two@out.com";' in s
    assert 'redirect "one@gmail.com";' not in s   # no bare (non-copy) redirect
    assert s.count("stop;") == 1                   # only the spam-branch stop

def test_build_sieve_empty_is_valid_noop():
    s = _load().build_sieve([])
    assert s.strip().startswith("require")

def test_write_script_atomic_and_compiles_optional(tmp_path):
    mod = _load()
    out = tmp_path / "forward.sieve"
    mod.write_script('require ["envelope"];\n', str(out))
    assert out.read_text().startswith("require")
    # temp file must not linger
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_write_script_is_readable_by_dovecot(tmp_path):
    # Dovecot runs Sieve as vmail, not root; a 0600 root file (mkstemp default)
    # is unreadable and silently disables forwarding. It must be world/group
    # readable (0644), not owner-only.
    import os
    import stat
    mod = _load()
    out = tmp_path / "forward.sieve"
    mod.write_script('require ["envelope"];\n', str(out))
    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode & 0o044, f"forward.sieve not group/other readable: {oct(mode)}"
