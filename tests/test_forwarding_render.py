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
    # correct rspamd milter_headers API: activate the built-in 'spam-header'
    # routine and customise it to X-Spam: Yes via a routines{} block.
    assert '"spam-header"' in text
    assert "routines" in text
    assert 'header = "X-Spam"' in text
    assert 'value = "Yes"' in text
    # guard against the previously-fabricated keys/routine name
    assert '"x-spam-header"' not in text
    assert "spam_header_value" not in text

def test_sieve_before_conf_points_at_generated_script():
    tpl = REPO / "rootfs" / "tpl" / "dovecot" / "95-sieve.conf.tpl"
    assert tpl.is_file()
    text = tpl.read_text()
    assert "/var/lib/dovecot/sieve/forward.sieve" in text
    # Dovecot 2.4: a named sieve_script block with type=before (NOT the removed
    # 2.3 `plugin { sieve_before }` form).
    assert "sieve_script" in text
    assert "type = before" in text
    assert "plugin {" not in text

def test_render_map_has_sieve_conf():
    rm = (REPO / "rootfs" / "tpl" / "render.map").read_text()
    assert "tpl/dovecot/95-sieve.conf.tpl" in rm
    assert "/etc/dovecot/conf.d/95-sieve.conf" in rm
