from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def test_schema_has_forwardings_notify_trigger():
    sql = (REPO / "sql" / "schema.sql").read_text()
    assert "pg_notify('forwardings_changed'" in sql
    assert "AFTER INSERT OR UPDATE OR DELETE ON forwardings" in sql
    assert "FOR EACH STATEMENT" in sql

def test_virtual_alias_skips_local_users():
    tpl = (REPO / "sql" / "postfix" / "virtual_alias_maps.cf.tpl").read_text()
    q = tpl.lower()
    # forwarding is only applied when the source is NOT a local mailbox user.
    assert "not exists" in q and "from users" in q
    # keep_copy self-mapping must be gone (Sieve now owns keep-copy semantics).
    assert "keep_copy" not in q

def test_rspamd_milter_headers_adds_x_spam():
    tpl = (REPO / "rootfs" / "tpl" / "rspamd" / "local.d" / "milter_headers.conf.tpl")
    assert tpl.is_file(), "milter_headers.conf.tpl missing"
    text = tpl.read_text()
    assert 'spam_header' in text
    assert '"X-Spam"' in text and '"Yes"' in text
