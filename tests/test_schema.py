"""Schema structure + the two correctness-critical UNION lookup queries.

These mirror the SPEC's KEY QUERIES verbatim so the rendered Postfix pgsql maps
(phases D/E) stay byte-identical to what is proven correct here.
"""
import pytest

# --- exact query text copied from the spec (KEY QUERIES) --------------------
# sender_login_maps (send-as): %s = envelope MAIL FROM (full email)
SENDER_LOGIN_MAPS_Q = """
SELECT email       FROM users             WHERE email=lower(%(k)s)          AND active
UNION
SELECT login_email FROM sender_login_maps WHERE allowed_sender=lower(%(k)s) AND active
"""

# virtual_alias_maps (forwarding + optional local copy): %s = recipient
VIRTUAL_ALIAS_MAPS_Q = """
SELECT destination FROM forwardings WHERE source=lower(%(k)s) AND active
UNION
SELECT lower(%(k)s)
 WHERE EXISTS (SELECT 1 FROM forwardings f
                WHERE f.source=lower(%(k)s) AND f.active AND f.keep_copy)
   AND EXISTS (SELECT 1 FROM users u
                WHERE u.email=lower(%(k)s) AND u.active)
"""

EXPECTED_TABLES = {
    "domains",
    "users",
    "forwardings",
    "sender_login_maps",
    "audit_logs",
}


def _columns(cur, table):
    cur.execute(
        """SELECT column_name, data_type, is_nullable, column_default
             FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position""",
        (table,),
    )
    return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Structure: tables, columns, constraints, indexes, roles, grants
# ---------------------------------------------------------------------------
def test_all_tables_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'"
        )
        present = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(present), EXPECTED_TABLES - present


def test_domains_columns(conn):
    with conn.cursor() as cur:
        cols = _columns(cur, "domains")
    assert set(cols) == {"id", "domain", "dkim_selector", "active", "created_at"}
    assert cols["domain"][0] == "text"
    assert cols["active"][0] == "boolean"
    assert "now()" in (cols["created_at"][2] or "")
    assert "'default'" in (cols["dkim_selector"][2] or "")


def test_users_columns(conn):
    with conn.cursor() as cur:
        cols = _columns(cur, "users")
    assert set(cols) == {
        "id", "email", "domain_id", "password",
        "quota_bytes", "active", "created_at",
    }
    assert cols["email"][0] == "text"
    assert cols["quota_bytes"][0] == "bigint"
    assert cols["domain_id"][0] == "bigint"


def test_forwardings_columns(conn):
    with conn.cursor() as cur:
        cols = _columns(cur, "forwardings")
    assert set(cols) == {
        "id", "source", "destination", "keep_copy", "active", "created_at",
    }
    assert cols["keep_copy"][0] == "boolean"


def test_sender_login_maps_columns(conn):
    with conn.cursor() as cur:
        cols = _columns(cur, "sender_login_maps")
    assert set(cols) == {
        "id", "login_email", "allowed_sender", "active", "created_at",
    }


def test_audit_logs_columns(conn):
    with conn.cursor() as cur:
        cols = _columns(cur, "audit_logs")
    assert set(cols) == {
        "id", "event_type", "success", "login", "src_ip", "host",
        "sender", "recipient", "message_id", "queue_id", "score",
        "msg", "pid", "timestamp",
    }
    assert cols["src_ip"][0] == "inet"
    assert cols["score"][0] == "real"


def test_unique_and_fk_constraints(conn):
    with conn.cursor() as cur:
        # domains.domain UNIQUE
        cur.execute(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name='domains' AND constraint_type='UNIQUE'"
        )
        assert cur.fetchone()
        # users.email UNIQUE
        cur.execute(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name='users' AND constraint_type='UNIQUE'"
        )
        assert cur.fetchone()
        # users.domain_id FK -> domains
        cur.execute(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name='users' AND constraint_type='FOREIGN KEY'"
        )
        assert cur.fetchone()
        # sender_login_maps UNIQUE(login_email, allowed_sender)
        cur.execute(
            """SELECT 1
                 FROM information_schema.table_constraints tc
                 JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name='sender_login_maps'
                  AND tc.constraint_type='UNIQUE'
                  AND kcu.column_name IN ('login_email','allowed_sender')
                GROUP BY tc.constraint_name
               HAVING count(*) = 2"""
        )
        assert cur.fetchone()


def test_pk_columns_are_bigint(conn):
    with conn.cursor() as cur:
        for table in EXPECTED_TABLES:
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='id'",
                (table,),
            )
            assert cur.fetchone()[0] == "bigint", table


def test_roles_exist(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN %s",
                    (("mail-server-ro", "mail-server-audit", "mail-server-admin"),))
        roles = {r[0] for r in cur.fetchall()}
    assert {"mail-server-ro", "mail-server-audit", "mail-server-admin"} == roles


def test_grants(conn):
    with conn.cursor() as cur:
        # mail-server-ro can SELECT the four lookup tables
        cur.execute(
            """SELECT table_name, privilege_type
                 FROM information_schema.role_table_grants
                WHERE grantee='mail-server-ro'"""
        )
        ro = {(t, p) for t, p in cur.fetchall()}
        for t in ("domains", "users", "forwardings", "sender_login_maps"):
            assert (t, "SELECT") in ro, (t, ro)
        # mail-server-audit can INSERT audit_logs
        cur.execute(
            """SELECT privilege_type
                 FROM information_schema.role_table_grants
                WHERE grantee='mail-server-audit' AND table_name='audit_logs'"""
        )
        audit = {r[0] for r in cur.fetchall()}
        assert "INSERT" in audit
        # mail-server-admin has full CRUD on the four management tables
        cur.execute(
            """SELECT table_name, privilege_type
                 FROM information_schema.role_table_grants
                WHERE grantee='mail-server-admin'"""
        )
        rw = {(t, p) for t, p in cur.fetchall()}
        for t in ("domains", "users", "forwardings", "sender_login_maps"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert (t, priv) in rw, (t, priv, rw)
        assert ("audit_logs", "SELECT") in rw


# ---------------------------------------------------------------------------
# Helpers to seed fixture rows inside the test's rolled-back transaction
# ---------------------------------------------------------------------------
def _seed_domain_user(cur, email):
    cur.execute(
        "INSERT INTO domains (domain) VALUES (%s) "
        "ON CONFLICT (domain) DO UPDATE SET domain=EXCLUDED.domain "
        "RETURNING id",
        (email.split("@", 1)[1],),
    )
    domain_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO users (email, domain_id, password) VALUES (%s, %s, %s) "
        "RETURNING id",
        (email, domain_id, "{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=1$x$y"),
    )
    return domain_id


# ---------------------------------------------------------------------------
# sender_login_maps UNION: self-send + delegated grant
# ---------------------------------------------------------------------------
def test_sender_login_maps_self_send(conn):
    with conn.cursor() as cur:
        _seed_domain_user(cur, "alice@example.test")
        cur.execute(SENDER_LOGIN_MAPS_Q, {"k": "alice@example.test"})
        logins = {r[0] for r in cur.fetchall()}
    # a user may always send as their own address (implicit self-rule)
    assert logins == {"alice@example.test"}


def test_sender_login_maps_delegated_grant(conn):
    with conn.cursor() as cur:
        _seed_domain_user(cur, "alice@example.test")
        _seed_domain_user(cur, "bob@example.test")
        # grant: bob is allowed to send AS alice
        cur.execute(
            "INSERT INTO sender_login_maps (login_email, allowed_sender) "
            "VALUES (%s, %s)",
            ("bob@example.test", "alice@example.test"),
        )
        cur.execute(SENDER_LOGIN_MAPS_Q, {"k": "alice@example.test"})
        logins = {r[0] for r in cur.fetchall()}
    # alice (self) + bob (granted) may both use MAIL FROM alice@example.test
    assert logins == {"alice@example.test", "bob@example.test"}


def test_sender_login_maps_case_insensitive(conn):
    with conn.cursor() as cur:
        _seed_domain_user(cur, "alice@example.test")
        cur.execute(SENDER_LOGIN_MAPS_Q, {"k": "Alice@Example.TEST"})
        logins = {r[0] for r in cur.fetchall()}
    assert logins == {"alice@example.test"}


# ---------------------------------------------------------------------------
# virtual_alias_maps UNION: keep_copy guard
# ---------------------------------------------------------------------------
def test_virtual_alias_plain_redirect(conn):
    """A plain (keep_copy=false) row returns only the destination(s)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO forwardings (source, destination, keep_copy) "
            "VALUES (%s, %s, false)",
            ("fwd@example.test", "external@elsewhere.test"),
        )
        cur.execute(VIRTUAL_ALIAS_MAPS_Q, {"k": "fwd@example.test"})
        dests = {r[0] for r in cur.fetchall()}
    assert dests == {"external@elsewhere.test"}


def test_virtual_alias_keep_copy_real_mailbox(conn):
    """keep_copy=true AND source is a real active mailbox -> dest + self."""
    with conn.cursor() as cur:
        _seed_domain_user(cur, "carol@example.test")
        cur.execute(
            "INSERT INTO forwardings (source, destination, keep_copy) "
            "VALUES (%s, %s, true)",
            ("carol@example.test", "external@elsewhere.test"),
        )
        cur.execute(VIRTUAL_ALIAS_MAPS_Q, {"k": "carol@example.test"})
        dests = {r[0] for r in cur.fetchall()}
    assert dests == {"external@elsewhere.test", "carol@example.test"}


def test_virtual_alias_keep_copy_not_a_mailbox(conn):
    """keep_copy=true but source is NOT a mailbox -> dest only, no self."""
    with conn.cursor() as cur:
        # no users row for ghost@example.test
        cur.execute(
            "INSERT INTO forwardings (source, destination, keep_copy) "
            "VALUES (%s, %s, true)",
            ("ghost@example.test", "external@elsewhere.test"),
        )
        cur.execute(VIRTUAL_ALIAS_MAPS_Q, {"k": "ghost@example.test"})
        dests = {r[0] for r in cur.fetchall()}
    # self NOT added: the guard requires an active users row for the source
    assert dests == {"external@elsewhere.test"}


def test_virtual_alias_inactive_forwarding_ignored(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO forwardings (source, destination, active) "
            "VALUES (%s, %s, false)",
            ("off@example.test", "external@elsewhere.test"),
        )
        cur.execute(VIRTUAL_ALIAS_MAPS_Q, {"k": "off@example.test"})
        dests = {r[0] for r in cur.fetchall()}
    assert dests == set()
