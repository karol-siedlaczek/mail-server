"""mail-dkim-keygen: generate a per-domain DKIM key and print the DNS TXT.

Stubs `rspamadm` so the test is hermetic: the stub writes a dummy private key
to the -k path and prints a fake TXT record to stdout, which is exactly the
contract the helper relies on.
"""
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "images" / "mail-server" / "rootfs" / "usr" / "local" / "bin" / "mail-dkim-keygen"


def _stub_rspamadm(bindir: Path):
    stub = bindir / "rspamadm"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "# parse: rspamadm dkim_keygen -d <domain> -s <selector> -k <keypath> -b 2048\n"
        "key=''\n"
        "while [ $# -gt 0 ]; do case \"$1\" in -k) key=\"$2\"; shift 2;; *) shift;; esac; done\n"
        "printf 'PRIVKEY\\n' > \"$key\"\n"
        "printf 'selector._domainkey.dom IN TXT ( \"v=DKIM1; k=rsa; p=AAAA\" )\\n'\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run(tmp_path, *args, extra_env=None):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub_rspamadm(bindir)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["RSPAMD_DKIM_DIR_KEYS"] = str(tmp_path / "keys")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(HELPER), *args],
                          env=env, capture_output=True, text=True)


def test_usage_without_domain(tmp_path):
    r = run(tmp_path)
    assert r.returncode != 0
    assert "usage" in (r.stderr + r.stdout).lower()


def test_generates_key_and_prints_txt(tmp_path):
    r = run(tmp_path, "example.test", "sel1")
    assert r.returncode == 0, r.stderr
    key = tmp_path / "keys" / "example.test.sel1.key"
    assert key.exists()
    # private key must not be world-readable
    mode = key.stat().st_mode
    assert not (mode & stat.S_IROTH)
    # DNS TXT printed for the operator to publish
    assert "v=DKIM1" in r.stdout
    assert "_domainkey" in r.stdout


def test_default_selector(tmp_path):
    r = run(tmp_path, "example.test")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "keys" / "example.test.default.key").exists()
