"""Phase H — postsrsd 1.x render / configuration assertions.

SPEC DEFECT RECONCILIATION: Debian 13 ships postsrsd 1.x (not 2.x).
postsrsd 1.x does NOT read a postsrsd.conf — configuration is passed as CLI
flags in the s6 run script:
    postsrsd -s /etc/postsrsd.secret -d <domain> -f 10001 -r 10002

This module therefore asserts:
  1. render-config.sh derives SRS_DOMAIN = domain(MAIL_HOSTNAME) correctly
     (strip the leftmost label: mail.example.test -> example.test).
  2. The postsrsd s6 run script uses the 1.x -d/-f/-r/-s flag form.
  3. The run script uses the correct forward (10001) and reverse (10002) ports
     that match main.cf's tcp:localhost:10001 / tcp:localhost:10002 maps.

References:
  _contract.md § "SPEC DEFECT — postsrsd is 1.x on Debian 13 (affects Phase H)"
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]          # repo root (image build context)
RUN  = REPO / "rootfs" / "etc" / "s6-overlay" / "s6-rc.d" / "postsrsd" / "run"

# ── render-config.sh helpers (from conftest) ─────────────────────────────────
from conftest import run_render, parse_dump

REQUIRED_ENV = {
    "MAIL_HOSTNAME": "mail.example.test",
    "PG_HOST": "db",
    "PG_PORT": "5432",
    "PG_DBNAME": "maildb",
    "PG_USER": "mail-server-ro_user",
    "PG_PASSWORD": "secret",
}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def dump_example():
    """Resolved env dump for MAIL_HOSTNAME=mail.example.test."""
    proc = run_render(env=REQUIRED_ENV, dump_env=True)
    return parse_dump(proc.stdout)


@pytest.fixture(scope="module")
def dump_multi_label():
    """Resolved env dump for MAIL_HOSTNAME with a multi-label domain."""
    env = dict(REQUIRED_ENV)
    env["MAIL_HOSTNAME"] = "mail.siedlaczek.org.pl"
    proc = run_render(env=env, dump_env=True)
    return parse_dump(proc.stdout)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SRS_DOMAIN derivation in render-config.sh
# ─────────────────────────────────────────────────────────────────────────────

def test_srs_domain_strips_first_label(dump_example):
    """SRS_DOMAIN = domain(MAIL_HOSTNAME): strip the leftmost host label."""
    assert dump_example.get("SRS_DOMAIN") == "example.test", dump_example


def test_srs_domain_multi_label(dump_multi_label):
    """Works for 4-label hostnames: mail.siedlaczek.org.pl -> siedlaczek.org.pl."""
    assert dump_multi_label.get("SRS_DOMAIN") == "siedlaczek.org.pl", dump_multi_label


# ─────────────────────────────────────────────────────────────────────────────
# 2. postsrsd 1.x run script flags
# ─────────────────────────────────────────────────────────────────────────────

def test_run_script_exists():
    assert RUN.is_file(), f"missing {RUN}"


def test_run_script_uses_1x_flags():
    """The run script must use postsrsd 1.x CLI flags, NOT the 2.x -C flag.

    Only inspect non-comment lines so a comment like '# Do NOT use -C' does
    not trigger a false negative.
    """
    lines = RUN.read_text().splitlines()
    code_lines = [l for l in lines if not l.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "postsrsd" in code, "run script does not invoke postsrsd"
    assert "-s /etc/postsrsd.secret" in code, "missing -s <secret-file> flag"
    assert "-f 10001" in code, "missing -f <forward-port> flag (must be 10001)"
    assert "-r 10002" in code, "missing -r <reverse-port> flag (must be 10002)"
    assert "-C " not in code, "run script must NOT use the 2.x -C flag"


def test_run_script_no_config_file_flag():
    """Confirm the 2.x config-file interface is absent (1.x has no -C).

    Only inspect non-comment lines so a comment like '# Do NOT use postsrsd.conf'
    does not trigger a false negative.
    """
    lines = RUN.read_text().splitlines()
    code_lines = [l for l in lines if not l.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "postsrsd.conf" not in code, (
        "postsrsd 1.x does not support postsrsd.conf; use CLI flags"
    )


def test_forward_port_matches_main_cf():
    """Forward port 10001 must match main.cf tcp:localhost:10001 map."""
    main_cf_tpl = REPO / "rootfs" / "tpl" / "postfix" / "main.cf.tpl"
    text = main_cf_tpl.read_text()
    assert "tcp:localhost:10001" in text, (
        "main.cf.tpl sender_canonical_maps must reference tcp:localhost:10001"
    )
    assert "tcp:localhost:10002" in text, (
        "main.cf.tpl recipient_canonical_maps must reference tcp:localhost:10002"
    )
