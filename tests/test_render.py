import textwrap
from conftest import run_render, parse_dump


def base_env():
    """Minimal env that satisfies required-var validation."""
    return {
        "MAIL_HOSTNAME": "mail.example.test",
        "PG_HOST": "postgres",
        "PG_DBNAME": "maildb",
        "PG_USER": "mail_ro",
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
    assert dump["PG_AUDIT_USER"] == "mail_ro"
    assert dump["PG_AUDIT_PASSWORD"] == "s3cret"


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
