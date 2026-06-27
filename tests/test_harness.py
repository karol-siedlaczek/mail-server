"""Self-tests for the mail-server test harness itself.

Runs under `make test` (no daemons): it only checks that the harness modules
import, the compose file parses, and seed.sql carries the contract rows. The
live psycopg2/imaplib/swaks paths are exercised by `make itest` once the image
and schema exist (phases B+).
"""
import pathlib

import yaml

import conftest

HERE = pathlib.Path(__file__).parent


def test_helpers_are_exported():
    for name in ("pg_dsn", "pg_connect", "imap_login", "swaks",
                 "wait_for_port", "read_sink"):
        assert hasattr(conftest, name), f"conftest is missing {name}"


def test_compose_file_parses():
    doc = yaml.safe_load((HERE / "compose.test.yml").read_text())
    services = doc["services"]
    for svc in ("postgres", "redis", "sink", "mail-server", "clamav"):
        assert svc in services, f"compose missing service {svc}"
    # clamav must be gated behind the 'av' profile so default itest skips it.
    assert services["clamav"].get("profiles") == ["av"]


def test_seed_has_contract_rows():
    seed = (HERE / "seed.sql").read_text()
    for needle in ("example.test", "'test'", "alice@example.test",
                   "bob@example.test", "fwd@example.test", "keep_copy",
                   "sender_login_maps"):
        assert needle in seed, f"seed.sql missing {needle}"
