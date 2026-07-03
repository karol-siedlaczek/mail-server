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
    assert 'if not header :contains "X-Spam" "Yes"' in s
    assert 'redirect "karol@gmail.com";' in s
    assert "stop;" in s
    assert ":copy" not in s

def test_build_sieve_keep_copy_uses_copy_and_no_stop():
    s = _load().build_sieve([("a@ex.pl", "a@gmail.com", True)])
    assert 'redirect :copy "a@gmail.com";' in s
    assert "stop;" not in s

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

def test_build_sieve_empty_is_valid_noop():
    s = _load().build_sieve([])
    assert s.strip().startswith("require")
