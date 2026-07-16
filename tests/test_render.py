import os
import stat
import subprocess
from pathlib import Path
from conftest import run_render, parse_dump, ROOT


def base_env():
    """Minimal env that satisfies required-var validation."""
    return {
        "MAIL_HOSTNAME": "mail.example.test",
        "PG_HOST": "postgres",
        "PG_DBNAME": "maildb",
        "PG_USER": "mail-server-ro_user",
        "PG_PASSWORD": "s3cret",
        "REDIS_HOST": "redis",
    }


def test_defaults_applied():
    env = base_env()
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["PG_PORT"] == "5432"
    assert dump["REDIS_PORT"] == "6379"
    assert dump["REDIS_DB"] == "0"
    assert dump["REDIS_PREFIX"] == "mail"
    assert dump["CLAMAV_PORT"] == "3310"
    assert dump["CLAMAV_ENABLED"] == "true"
    assert dump["TLS_CERT_FILE"] == "/tls/fullchain.pem"
    assert dump["TLS_KEY_FILE"] == "/tls/privkey.pem"
    assert dump["PASSWORD_SCHEME"] == "ARGON2ID"
    assert dump["ALLOW_WEAK_SCHEMES"] == "false"
    assert dump["MESSAGE_SIZE_LIMIT"] == "52428800"
    assert dump["RSPAMD_REJECT_SCORE"] == "15"
    assert dump["DMARC_REPORT_ENABLED"] == "false"
    assert dump["AUDIT_ENABLED"] == "true"
    assert dump["AUDIT_SCOPE"] == "full"
    assert dump["POP3_ENABLED"] == "false"
    assert dump["POSTSCREEN_ENABLED"] == "true"
    assert dump["GREYLISTING_ENABLED"] == "true"


def test_explicit_value_overrides_default():
    env = base_env()
    env["REDIS_PORT"] = "6380"
    env["RSPAMD_REJECT_SCORE"] = "20"
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["REDIS_PORT"] == "6380"
    assert dump["RSPAMD_REJECT_SCORE"] == "20"


def test_file_secret_resolved(tmp_path):
    secret = tmp_path / "pgpw"
    secret.write_text("file-password\n")
    env = base_env()
    del env["PG_PASSWORD"]
    env["PG_PASSWORD__FILE"] = str(secret)
    dump = parse_dump(run_render(env=env).stdout)
    # trailing newline stripped, value loaded into the bare var
    assert dump["PG_PASSWORD"] == "file-password"


def test_file_secret_does_not_override_explicit(tmp_path):
    secret = tmp_path / "pgpw"
    secret.write_text("from-file\n")
    env = base_env()
    env["PG_PASSWORD"] = "from-env"
    env["PG_PASSWORD__FILE"] = str(secret)
    # explicit bare var wins; __FILE only fills an unset/empty var
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["PG_PASSWORD"] == "from-env"


def test_missing_file_secret_is_fatal(tmp_path):
    env = base_env()
    del env["PG_PASSWORD"]
    env["PG_PASSWORD__FILE"] = str(tmp_path / "nope")
    proc = run_render(env=env, expect_rc=1)
    assert "PG_PASSWORD__FILE" in proc.stderr


def test_audit_creds_default_to_pg_creds():
    env = base_env()
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["PG_AUDIT_USER"] == "mail-server-ro_user"
    assert dump["PG_AUDIT_PASSWORD"] == "s3cret"


def test_audit_policy_nonce_generated_when_audit_enabled():
    """AUDIT_POLICY_NONCE is auto-generated (non-empty) when AUDIT_ENABLED is true."""
    env = base_env()
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["AUDIT_ENABLED"] == "true"
    assert dump["AUDIT_POLICY_NONCE"] != "", "AUDIT_POLICY_NONCE must be non-empty"


def test_audit_policy_nonce_preserved_when_provided():
    """An explicit AUDIT_POLICY_NONCE is passed through unchanged."""
    env = base_env()
    env["AUDIT_POLICY_NONCE"] = "deadbeef1234"
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["AUDIT_POLICY_NONCE"] == "deadbeef1234"


def test_audit_policy_block_populated_when_enabled():
    """AUDIT_POLICY_BLOCK contains the auth-policy URL when AUDIT_ENABLED=true."""
    env = base_env()
    env["AUDIT_ENABLED"] = "true"
    dump = parse_dump(run_render(env=env).stdout)
    assert "auth_policy_server_url" in dump["AUDIT_POLICY_BLOCK"], (
        "AUDIT_POLICY_BLOCK must contain auth_policy_server_url when AUDIT_ENABLED=true"
    )
    assert "http://127.0.0.1:4001/" in dump["AUDIT_POLICY_BLOCK"]


def test_audit_policy_block_empty_when_disabled():
    """AUDIT_POLICY_BLOCK is empty when AUDIT_ENABLED=false."""
    env = base_env()
    env["AUDIT_ENABLED"] = "false"
    dump = parse_dump(run_render(env=env).stdout)
    assert dump["AUDIT_POLICY_BLOCK"] == "", (
        "AUDIT_POLICY_BLOCK must be empty when AUDIT_ENABLED=false"
    )


def test_missing_required_hostname_is_fatal():
    env = base_env()
    del env["MAIL_HOSTNAME"]
    proc = run_render(env=env, expect_rc=1)
    assert "MAIL_HOSTNAME" in proc.stderr


def test_missing_required_pg_is_fatal():
    env = base_env()
    del env["PG_DBNAME"]
    proc = run_render(env=env, expect_rc=1)
    assert "PG_DBNAME" in proc.stderr


def full_render(tmp_path, extra=None):
    """Run a *real* (non-dump) render into tmp_path via RENDER_ROOT.

    Returns the render root so tests can read rendered files at
    <root><absolute-dest>.
    """
    env = base_env()
    if extra:
        env.update(extra)
    env["PATH"] = os.environ["PATH"]
    env["RENDER_ROOT"] = str(tmp_path)
    # Skip the Postgres DKIM-map SELECT: these tests have no live DB.
    # RSPAMD_LOCALD_DIR/RSPAMD_DKIM_DIR default to ${RENDER_ROOT}/etc/rspamd/*
    # so no explicit override is needed here.
    env.setdefault("RSPAMD_SKIP_DB", "1")
    proc = subprocess.run(
        ["bash", str(ROOT / "rootfs/usr/local/bin/render-config.sh")],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return tmp_path


def test_smoke_template_rendered(tmp_path):
    root = full_render(tmp_path)
    out = (root / "run/mail-render-smoke.conf").read_text()
    assert "hostname = mail.example.test" in out
    assert "pg = mail-server-ro_user@postgres:5432/maildb" in out
    assert "redis = redis:6379/0 prefix=mail" in out
    assert "reject_score = 15" in out
    # no unsubstituted placeholders survived
    assert "${" not in out


def test_tls_split_layout_default(tmp_path):
    # No TLS_CHAIN_FILE → historical split: Postfix lists key first then cert,
    # Dovecot points cert/key at the two separate files.
    root = full_render(tmp_path)
    maincf = (root / "etc/postfix/main.cf").read_text()
    assert "smtpd_tls_chain_files = /tls/privkey.pem /tls/fullchain.pem" in maincf
    ssl = (root / "etc/dovecot/conf.d/10-ssl.conf").read_text()
    assert "ssl_server_cert_file = /tls/fullchain.pem" in ssl
    assert "ssl_server_key_file = /tls/privkey.pem" in ssl


def test_tls_chain_file_single_pem(tmp_path):
    # TLS_CHAIN_FILE set → one combined PEM drives both daemons.
    root = full_render(tmp_path, extra={"TLS_CHAIN_FILE": "/tls/combined.pem"})
    maincf = (root / "etc/postfix/main.cf").read_text()
    assert "smtpd_tls_chain_files = /tls/combined.pem" in maincf
    ssl = (root / "etc/dovecot/conf.d/10-ssl.conf").read_text()
    assert "ssl_server_cert_file = /tls/combined.pem" in ssl
    assert "ssl_server_key_file = /tls/combined.pem" in ssl
    # render-config minted a self-signed combined file, key-first.
    chain = (root / "tls/combined.pem").read_text()
    assert "BEGIN PRIVATE KEY" in chain or "BEGIN RSA PRIVATE KEY" in chain
    assert chain.index("PRIVATE KEY") < chain.index("BEGIN CERTIFICATE")


def test_relayhost_sasl_absent_by_default(tmp_path):
    # No RELAYHOST_USER → no SASL client directives leak into main.cf, and the
    # ${POSTFIX_RELAYHOST_SASL} placeholder is fully substituted (to empty).
    root = full_render(tmp_path)
    maincf = (root / "etc/postfix/main.cf").read_text()
    assert "smtp_sasl_auth_enable = yes" not in maincf
    assert "smtp_sasl_password_maps" not in maincf
    assert "${POSTFIX_RELAYHOST_SASL}" not in maincf


def test_relayhost_sasl_enabled_when_user_set(tmp_path):
    # RELAYHOST_USER set → SASL client auth toward the smarthost is wired, with
    # the creds in a static: map and plaintext mechanisms (noanonymous) allowed.
    root = full_render(tmp_path, extra={
        "RELAYHOST": "[smtp.relay.test]:587",
        "RELAYHOST_USER": "relayuser",
        "RELAYHOST_PASSWORD": "relaypass",
    })
    maincf = (root / "etc/postfix/main.cf").read_text()
    assert "relayhost = [smtp.relay.test]:587" in maincf
    assert "smtp_sasl_auth_enable = yes" in maincf
    assert "smtp_sasl_password_maps = static:relayuser:relaypass" in maincf
    assert "smtp_sasl_security_options = noanonymous" in maincf


def test_render_creates_dest_dirs(tmp_path):
    root = full_render(tmp_path)
    assert (root / "run/mail-render-smoke.conf").is_file()


def test_selfsigned_cert_generated_when_absent(tmp_path):
    # default TLS_CERT_FILE/_KEY_FILE point at /tls/* which do not exist under
    # the render root, so render-config must mint a self-signed pair.
    root = full_render(tmp_path)
    cert = root / "tls/fullchain.pem"
    key = root / "tls/privkey.pem"
    assert cert.is_file() and key.is_file()
    assert "BEGIN CERTIFICATE" in cert.read_text()
    # key must not be world-readable
    mode = stat.S_IMODE(key.stat().st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_existing_cert_not_overwritten(tmp_path):
    cert = tmp_path / "tls/fullchain.pem"
    key = tmp_path / "tls/privkey.pem"
    cert.parent.mkdir(parents=True)
    cert.write_text("PRESEEDED-CERT\n")
    key.write_text("PRESEEDED-KEY\n")
    full_render(tmp_path)
    assert cert.read_text() == "PRESEEDED-CERT\n"
    assert key.read_text() == "PRESEEDED-KEY\n"
