from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def test_schema_has_forwardings_notify_trigger():
    sql = (REPO / "sql" / "schema.sql").read_text()
    assert "pg_notify('forwardings_changed'" in sql
    assert "AFTER INSERT OR UPDATE OR DELETE ON forwardings" in sql
    assert "FOR EACH STATEMENT" in sql
