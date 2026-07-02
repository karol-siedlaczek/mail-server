# Sieve ham-only forwarding (psql-driven, LISTEN/NOTIFY sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Forward mail addressed to hosted mailboxes to external addresses (Gmail/Outlook) **only when the message is not spam**, so the server stops relaying spam and protects its sending reputation — while keeping `forwardings` in Postgres as the single source of truth that `mail-controller` writes.

**Architecture:** Forwarding moves out of Postfix `virtual_alias_maps` (which redirects before the message ever reaches Dovecot) into a Dovecot **`sieve_before`** global script. A small daemon (`sieve-forward-sync`) regenerates that script from the `forwardings` table and stays current via Postgres **LISTEN/NOTIFY** (a statement-level trigger on `forwardings` fires `pg_notify`). The Sieve script gates each `redirect` on a deterministic rspamd spam header (`X-Spam: Yes`). Postfix keeps redirecting only *non-mailbox* aliases (unchanged); mailboxed addresses now deliver locally and Sieve does the (filtered) forward.

**Tech Stack:** Postfix 3.10, Dovecot 2.4 + Pigeonhole Sieve, Rspamd 4.1 (milter), Postgres (external), Python 3 + psycopg2 (already installed), s6-overlay v3, bash `render-config.sh`, pytest.

## Global Constraints

- `forwardings` (psql) stays the source of truth; **`mail-controller`'s write path does not change** — it keeps inserting/updating `forwardings` rows. The only DB addition is a NOTIFY trigger.
- The lookup role `mail-server-ro` (env `PG_USER`) is **SELECT-only**; the sync daemon connects as it. `LISTEN` needs no table privilege. Do not require new grants beyond existing `SELECT ON forwardings, users`.
- Templates render through `render-config.sh`; every `${VAR}` must be in `DUMP_VARS` or it will not be substituted. Unit tests run `render-config.sh` with `RENDER_ROOT=<tmp>` and must stay green (`python3 -m pytest tests/ -k "not itest and not integration"`).
- s6 longruns: a service that must not run still has to stay "up" (never exit in a loop). New daemons block in the foreground under s6.
- Spam/ham line = rspamd `add header` action (default score ≥ 6 in `actions.conf.tpl`). "Spam" for forwarding = presence of header `X-Spam: Yes`.
- No secrets in logs. The daemon must never print `PG_PASSWORD`.
- Keep changes DRY/YAGNI; follow existing file/comment style. Commit after every task.

---

## File Structure

**Created:**
- `rootfs/usr/local/bin/sieve-forward-sync` — Python daemon: query `forwardings`, build the Sieve script, LISTEN/NOTIFY + periodic fallback, atomic write + `sievec` compile. Contains the pure function `build_sieve(rows)`.
- `rootfs/tpl/rspamd/local.d/milter_headers.conf.tpl` — force a deterministic `X-Spam: Yes` header on the add-header action (the Sieve gate).
- `rootfs/tpl/dovecot/95-sieve.conf.tpl` — Dovecot Sieve settings: `sieve_before` → generated script; keep personal ManageSieve scripts working.
- `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/{type,run}` and `dependencies.d/{render-config,unbound,postgres-ready}` and `user/contents.d/sieve-forward-sync`.
- `tests/test_sieve_forward.py` — unit tests for `build_sieve`.
- `tests/test_forwarding_render.py` — render assertions for the new virtual_alias query, milter_headers, sieve conf, and schema trigger presence.

**Modified:**
- `sql/postfix/virtual_alias_maps.cf.tpl` — stop returning external destinations for addresses that are local mailboxes (deliver locally so Sieve forwards); keep plain aliasing for non-mailbox sources.
- `sql/schema.sql` — add `notify_forwardings_changed()` + statement trigger on `forwardings`.
- `rootfs/tpl/render.map` — add the `95-sieve.conf` mapping.

**Runtime artifact (not in git):** `/var/lib/dovecot/sieve/forward.sieve` (+ `.svbin`), written by the daemon into the persistent `/var/lib/dovecot` volume.

---

## Task 1: Postgres NOTIFY trigger on `forwardings`

**Files:**
- Modify: `sql/schema.sql` (append after the `forwardings` table + index block, around `sql/schema.sql:39`)
- Test: `tests/test_forwarding_render.py`

**Interfaces:**
- Produces: a psql channel `forwardings_changed` that fires (payload empty) on every INSERT/UPDATE/DELETE statement against `forwardings`. Consumed by the daemon in Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forwarding_render.py
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def test_schema_has_forwardings_notify_trigger():
    sql = (REPO / "sql" / "schema.sql").read_text()
    assert "pg_notify('forwardings_changed'" in sql
    assert "AFTER INSERT OR UPDATE OR DELETE ON forwardings" in sql
    assert "FOR EACH STATEMENT" in sql
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_schema_has_forwardings_notify_trigger -v`
Expected: FAIL (`pg_notify('forwardings_changed'` not found).

- [ ] **Step 3: Append the trigger to `sql/schema.sql`** (after the `forwardings_source_active_idx` index)

```sql
-- ── forwardings change notification (Sieve forward sync) ────────────────────
-- The mail-server sieve-forward-sync daemon LISTENs on 'forwardings_changed'
-- and regenerates the Sieve forward script. Statement-level (one NOTIFY per
-- statement, not per row). Runs as whoever writes forwardings (mail-controller);
-- pg_notify needs no special privilege.
CREATE OR REPLACE FUNCTION notify_forwardings_changed() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('forwardings_changed', '');
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS forwardings_notify ON forwardings;
CREATE TRIGGER forwardings_notify
  AFTER INSERT OR UPDATE OR DELETE ON forwardings
  FOR EACH STATEMENT EXECUTE FUNCTION notify_forwardings_changed();
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_schema_has_forwardings_notify_trigger -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sql/schema.sql tests/test_forwarding_render.py
git commit -m "feat(schema): NOTIFY on forwardings changes for Sieve sync"
```

---

## Task 2: Stop Postfix from redirecting mailboxed forwards

Local mailboxes must deliver via LMTP so Sieve can run and filter the forward. `virtual_alias_maps` therefore returns a destination **only when the source is not an active local user**. Mailboxed addresses return nothing → fall through to `virtual_mailbox_maps` → LMTP → Sieve.

**Files:**
- Modify: `sql/postfix/virtual_alias_maps.cf.tpl`
- Test: `tests/test_forwarding_render.py`

**Interfaces:**
- Consumes: `forwardings(source,destination,active)`, `users(email,active)`.
- Produces: the rendered `/etc/postfix/sql/virtual_alias_maps.cf` whose `query` returns rows **only for non-user sources**.

- [ ] **Step 1: Write the failing test**

```python
def test_virtual_alias_skips_local_users():
    tpl = (REPO / "sql" / "postfix" / "virtual_alias_maps.cf.tpl").read_text()
    q = tpl.lower()
    # forwarding is only applied when the source is NOT a local mailbox user.
    assert "not exists" in q and "from users" in q
    # keep_copy self-mapping must be gone (Sieve now owns keep-copy semantics).
    assert "keep_copy" not in q
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_virtual_alias_skips_local_users -v`
Expected: FAIL (current template contains `keep_copy` and no `NOT EXISTS ... users`).

- [ ] **Step 3: Rewrite the query in `sql/postfix/virtual_alias_maps.cf.tpl`**

Replace the `query = ...` block (keep the `hosts/dbname/user/password` lines) with:

```
# Forwarding is applied by Postfix ONLY for sources that are NOT local mailbox
# users. For local mailboxes the address is delivered to Dovecot (LMTP) and the
# sieve-forward-sync Sieve script does a spam-gated redirect instead, so we must
# NOT redirect them here (that would bypass Dovecot/rspamd filtering). Plain
# aliases (source with no mailbox) keep the classic unconditional redirect.
query = SELECT f.destination FROM forwardings f
         WHERE f.source = lower('%s') AND f.active
           AND NOT EXISTS (SELECT 1 FROM users u
                            WHERE u.email = lower('%s') AND u.active)
```

- [ ] **Step 4: Run test + full render suite, expect PASS**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_virtual_alias_skips_local_users tests/test_postfix_render.py -v`
Expected: PASS (no `${...}` left unrendered; existing postfix-render tests still green).

- [ ] **Step 5: Commit**

```bash
git add sql/postfix/virtual_alias_maps.cf.tpl tests/test_forwarding_render.py
git commit -m "feat(postfix): don't alias-redirect mailboxed forwards (Sieve handles them)"
```

---

## Task 3: Deterministic rspamd spam header for the Sieve gate

The Sieve gate keys on `X-Spam: Yes`. Make rspamd add it deterministically on the `add header` action instead of relying on defaults.

**Files:**
- Create: `rootfs/tpl/rspamd/local.d/milter_headers.conf.tpl`
- Test: `tests/test_forwarding_render.py`

**Interfaces:**
- Produces: `/etc/rspamd/local.d/milter_headers.conf` that adds header `X-Spam: Yes` when the action is `add header` (or above). Consumed logically by the Sieve script (Task 4).

Note: `render-config.sh` already renders every `rootfs/tpl/rspamd/local.d/*.tpl` (except antivirus) — no `render.map` entry needed for rspamd templates.

- [ ] **Step 1: Write the failing test**

```python
def test_rspamd_milter_headers_adds_x_spam():
    tpl = (REPO / "rootfs" / "tpl" / "rspamd" / "local.d" / "milter_headers.conf.tpl")
    assert tpl.is_file(), "milter_headers.conf.tpl missing"
    text = tpl.read_text()
    assert 'spam_header' in text
    assert '"X-Spam"' in text and '"Yes"' in text
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_rspamd_milter_headers_adds_x_spam -v`
Expected: FAIL (file missing).

- [ ] **Step 3: Create `rootfs/tpl/rspamd/local.d/milter_headers.conf.tpl`**

```
# Guarantee a deterministic spam marker for the Sieve forward gate. Rspamd adds
# "X-Spam: Yes" when the action is 'add header' (score >= the add_header action
# in actions.conf). The sieve-forward-sync script forwards a message ONLY when
# this header is absent, so spam is never relayed to external mailboxes.
use = ["x-spam-header", "x-spamd-result", "authentication-results"];
spam_header = "X-Spam";
spam_header_value = "Yes";
authenticated_headers = ["authentication-results"];
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_forwarding_render.py::test_rspamd_milter_headers_adds_x_spam -v`
Expected: PASS.

- [ ] **Step 5: (config sanity, optional if rspamd installed locally)**

Run: `command -v rspamadm && rspamadm configtest 2>&1 | tail -1 || echo "rspamadm not local — will verify in container"`
Expected: `syntax OK` or the skip message.

- [ ] **Step 6: Commit**

```bash
git add rootfs/tpl/rspamd/local.d/milter_headers.conf.tpl tests/test_forwarding_render.py
git commit -m "feat(rspamd): deterministic X-Spam header for Sieve forward gate"
```

---

## Task 4: `build_sieve` — pure generator (rows → Sieve script)

**Files:**
- Create: `rootfs/usr/local/bin/sieve-forward-sync` (this task adds only the module + `build_sieve`; the daemon `main()` comes in Task 5)
- Test: `tests/test_sieve_forward.py`

**Interfaces:**
- Produces: `build_sieve(rows: list[tuple[str, str, bool]]) -> str`, where each row is `(source_email, destination_email, keep_copy)`. Returns a complete Pigeonhole Sieve script. Consumed by the daemon in Task 5 and by tests.

Semantics encoded:
- Group by source. For each source emit one `if envelope :is "to" "<source>"` block.
- Forward only when **not** spam: wrap redirects in `if not header :contains "X-Spam" "Yes"`.
- `keep_copy = False` → `redirect "<dest>";` then `stop;` (redirect cancels implicit keep → no local copy).
- `keep_copy = True` → `redirect :copy "<dest>";` (keeps local copy; no `stop`).
- Multiple destinations for one source → multiple `redirect` lines. If **any** row for that source has `keep_copy=True`, keep the local copy (use `:copy` on all and omit `stop`).
- Escape `"` and `\` in addresses defensively.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sieve_forward.py
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MOD_PATH = REPO / "rootfs" / "usr" / "local" / "bin" / "sieve-forward-sync"

def _load():
    spec = importlib.util.spec_from_file_location("sfs", MOD_PATH)
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
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `python3 -m pytest tests/test_sieve_forward.py -v`
Expected: FAIL (module file missing / `build_sieve` undefined).

- [ ] **Step 3: Create `rootfs/usr/local/bin/sieve-forward-sync` with the generator**

```python
#!/usr/bin/env python3
"""Regenerate the Dovecot Sieve forward script from the psql `forwardings`
table, and keep it current via LISTEN/NOTIFY. See docs plan
2026-07-01-sieve-ham-forwarding. The daemon entrypoint is added in a later step;
build_sieve() is a pure function so it is unit-testable in isolation."""
from __future__ import annotations

HEADER = (
    "# GENERATED by sieve-forward-sync from psql `forwardings` — DO NOT EDIT.\n"
    "# Spam-gated forwarding: a message is redirected only when rspamd did NOT\n"
    "# mark it (header X-Spam: Yes), so spam is never relayed externally.\n"
)


def _q(value: str) -> str:
    """Quote a string for a Sieve double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_sieve(rows):
    """rows: iterable of (source, destination, keep_copy). Returns a Sieve script."""
    by_source = {}
    for source, dest, keep_copy in rows:
        entry = by_source.setdefault(source, {"dests": [], "keep": False})
        entry["dests"].append(dest)
        entry["keep"] = entry["keep"] or bool(keep_copy)

    lines = ['require ["envelope", "copy"];', "", HEADER.rstrip("\n")]
    for source in sorted(by_source):
        entry = by_source[source]
        keep = entry["keep"]
        lines.append(f'if envelope :is "to" "{_q(source)}" {{')
        lines.append('  if not header :contains "X-Spam" "Yes" {')
        for dest in entry["dests"]:
            if keep:
                lines.append(f'    redirect :copy "{_q(dest)}";')
            else:
                lines.append(f'    redirect "{_q(dest)}";')
        if not keep:
            lines.append("    stop;")
        lines.append("  }")
        lines.append("}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x rootfs/usr/local/bin/sieve-forward-sync`

- [ ] **Step 5: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_sieve_forward.py -v`
Expected: PASS (all 5).

- [ ] **Step 6: Commit**

```bash
git add rootfs/usr/local/bin/sieve-forward-sync tests/test_sieve_forward.py
git commit -m "feat(sieve): build_sieve generator (forwardings rows -> Sieve script)"
```

---

## Task 5: Sync daemon — query, atomic write, `sievec`, LISTEN/NOTIFY

**Files:**
- Modify: `rootfs/usr/local/bin/sieve-forward-sync` (add DB query, writer, and `main()` loop below `build_sieve`)
- Test: `tests/test_sieve_forward.py` (add a writer test that does not need a DB)

**Interfaces:**
- Consumes: env `PG_HOST, PG_PORT, PG_DBNAME, PG_USER, PG_PASSWORD`; channel `forwardings_changed` (Task 1); `build_sieve` (Task 4).
- Produces: writes `SIEVE_OUT` (default `/var/lib/dovecot/sieve/forward.sieve`) atomically and compiles `forward.svbin` via `sievec`. Function `write_script(text, out_path)` is unit-testable without a DB.

Query used at runtime:
```sql
SELECT f.source, f.destination, f.keep_copy
  FROM forwardings f JOIN users u ON u.email = f.source
 WHERE f.active AND u.active
```
(Only mailboxed sources — matches Task 2, which left non-mailbox aliases to Postfix.)

- [ ] **Step 1: Write the failing writer test**

```python
def test_write_script_atomic_and_compiles_optional(tmp_path):
    mod = _load()
    out = tmp_path / "forward.sieve"
    mod.write_script('require ["envelope"];\n', str(out))
    assert out.read_text().startswith("require")
    # temp file must not linger
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_sieve_forward.py::test_write_script_atomic_and_compiles_optional -v`
Expected: FAIL (`write_script` undefined).

- [ ] **Step 3: Append daemon code to `rootfs/usr/local/bin/sieve-forward-sync`**

```python
import os
import select
import subprocess
import sys
import tempfile
import time

SIEVE_OUT = os.environ.get("SIEVE_OUT", "/var/lib/dovecot/sieve/forward.sieve")
POLL_SECONDS = int(os.environ.get("SIEVE_SYNC_INTERVAL", "60"))  # fallback resync


def log(msg):
    print(f"sieve-forward-sync: {msg}", flush=True)


def fetch_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.source, f.destination, f.keep_copy "
            "FROM forwardings f JOIN users u ON u.email = f.source "
            "WHERE f.active AND u.active"
        )
        return cur.fetchall()


def write_script(text, out_path=SIEVE_OUT):
    """Atomically write the Sieve script; compile to .svbin when sievec exists.
    A compile failure is logged but never deletes the working script."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    d = os.path.dirname(out_path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if _which("sievec"):
        r = subprocess.run(["sievec", out_path], capture_output=True, text=True)
        if r.returncode != 0:
            log(f"WARNING: sievec failed: {r.stderr.strip()}")


def _which(prog):
    from shutil import which
    return which(prog)


def regenerate(conn):
    rows = fetch_rows(conn)
    write_script(build_sieve(rows))
    log(f"regenerated {SIEVE_OUT} ({len(rows)} forward row(s))")


def main():
    import psycopg2  # provided by python3-psycopg2 in the image
    dsn = dict(
        host=os.environ["PG_HOST"],
        port=os.environ.get("PG_PORT", "5432"),
        dbname=os.environ["PG_DBNAME"],
        user=os.environ["PG_USER"],
        password=os.environ.get("PG_PASSWORD", ""),
    )
    while True:
        try:
            conn = psycopg2.connect(**dsn)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("LISTEN forwardings_changed")
            log("connected; listening on forwardings_changed")
            regenerate(conn)
            while True:
                if select.select([conn], [], [], POLL_SECONDS) == ([], [], []):
                    regenerate(conn)          # periodic fallback resync
                    continue
                conn.poll()
                if conn.notifies:
                    conn.notifies.clear()
                    regenerate(conn)
        except Exception as exc:                # DB down / restart → back off
            log(f"error: {exc}; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run writer test, expect PASS**

Run: `python3 -m pytest tests/test_sieve_forward.py -v`
Expected: PASS (writer + all build_sieve tests).

- [ ] **Step 5: Commit**

```bash
git add rootfs/usr/local/bin/sieve-forward-sync tests/test_sieve_forward.py
git commit -m "feat(sieve): LISTEN/NOTIFY sync daemon with atomic write + sievec"
```

---

## Task 6: s6 longrun `sieve-forward-sync`

**Files:**
- Create: `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/type` (content: `longrun`)
- Create: `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/run`
- Create (empty): `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/dependencies.d/render-config`
- Create (empty): `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/dependencies.d/unbound`
- Create (empty): `rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/dependencies.d/postgres-ready`
- Create (empty): `rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/sieve-forward-sync`

**Interfaces:**
- Consumes: container env (via `with-contenv`), a reachable Postgres (hence the `postgres-ready` dep, which itself depends on `render-config` + `unbound`).
- Produces: the running daemon that maintains `/var/lib/dovecot/sieve/forward.sieve`.

- [ ] **Step 1: Create the service files**

```bash
cd rootfs/etc/s6-overlay/s6-rc.d
mkdir -p sieve-forward-sync/dependencies.d
printf 'longrun' > sieve-forward-sync/type
: > sieve-forward-sync/dependencies.d/render-config
: > sieve-forward-sync/dependencies.d/unbound
: > sieve-forward-sync/dependencies.d/postgres-ready
: > user/contents.d/sieve-forward-sync
```

- [ ] **Step 2: Write `sieve-forward-sync/run`**

```
#!/command/execlineb -P
with-contenv
fdmove -c 2 1
/usr/local/bin/sieve-forward-sync
```

- [ ] **Step 3: Verify structure (executable bit is set at build by the Dockerfile `find ... -name run` chmod)**

Run: `ls -R rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync && cat rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync/type`
Expected: `type`, `run`, `dependencies.d/{render-config,unbound,postgres-ready}` present; type prints `longrun`.

- [ ] **Step 4: Commit**

```bash
git add rootfs/etc/s6-overlay/s6-rc.d/sieve-forward-sync rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/sieve-forward-sync
git commit -m "feat(s6): supervise sieve-forward-sync daemon"
```

---

## Task 7: Dovecot `sieve_before` wiring

**Files:**
- Create: `rootfs/tpl/dovecot/95-sieve.conf.tpl`
- Modify: `rootfs/tpl/render.map` (add mapping in the dovecot section)
- Test: `tests/test_forwarding_render.py`

**Interfaces:**
- Consumes: the runtime script `/var/lib/dovecot/sieve/forward.sieve` (Task 5).
- Produces: `/etc/dovecot/conf.d/95-sieve.conf` that runs the global forward script before personal scripts, at LMTP delivery.

> **Syntax note (verify against Pigeonhole 2.4.1 in-container):** the widely-compatible form is a `plugin { sieve_before = ... }` block. If `doveconf -n` warns/errors on 2.4, switch to the 2.4 named form `sieve_script before { path = ... }`. Step 5 verifies this in the container before finishing.

- [ ] **Step 1: Write the failing test**

```python
def test_sieve_before_conf_points_at_generated_script():
    tpl = REPO / "rootfs" / "tpl" / "dovecot" / "95-sieve.conf.tpl"
    assert tpl.is_file()
    text = tpl.read_text()
    assert "/var/lib/dovecot/sieve/forward.sieve" in text
    assert "sieve_before" in text

def test_render_map_has_sieve_conf():
    rm = (REPO / "rootfs" / "tpl" / "render.map").read_text()
    assert "tpl/dovecot/95-sieve.conf.tpl" in rm
    assert "/etc/dovecot/conf.d/95-sieve.conf" in rm
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m pytest tests/test_forwarding_render.py -k sieve_before -v && python3 -m pytest tests/test_forwarding_render.py -k render_map -v`
Expected: FAIL (file + mapping missing).

- [ ] **Step 3: Create `rootfs/tpl/dovecot/95-sieve.conf.tpl`**

```
# Global Sieve run BEFORE any personal (ManageSieve) script, at LMTP delivery.
# The forward script is generated from psql `forwardings` by sieve-forward-sync
# and does the spam-gated external redirect. Personal scripts (~/.dovecot.sieve
# via ManageSieve) still run afterwards.
plugin {
  sieve_before = /var/lib/dovecot/sieve/forward.sieve
  sieve = file:~/sieve;active=~/.dovecot.sieve
}
```

- [ ] **Step 4: Add the mapping to `rootfs/tpl/render.map`** (in the dovecot group, after the `90-quota` line)

```
tpl/dovecot/95-sieve.conf.tpl                   /etc/dovecot/conf.d/95-sieve.conf
```

- [ ] **Step 5: Run tests + full render suite, expect PASS**

Run: `python3 -m pytest tests/test_forwarding_render.py tests/test_render.py tests/test_dovecot_render.py -q`
Expected: PASS. (In-container later: `doveconf -n | grep -i sieve_before` shows the path; adjust to the 2.4 `sieve_script before {}` form if `doveconf` errors.)

- [ ] **Step 6: Commit**

```bash
git add rootfs/tpl/dovecot/95-sieve.conf.tpl rootfs/tpl/render.map tests/test_forwarding_render.py
git commit -m "feat(dovecot): sieve_before global forward script"
```

---

## Task 8: End-to-end verification (build + live)

**Files:** none (verification only). Do not mark complete until every check passes on the running container.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/ -q -k "not itest and not integration"`
Expected: all pass (no regressions).

- [ ] **Step 2: Build the image**

Run: `docker build -t registry.siedlaczek.com.pl/mail-server:test .`
Expected: build succeeds.

- [ ] **Step 3: Apply the schema trigger to the live DB** (mail-controller's DB)

Run: `psql "$DBURL" -f sql/schema.sql`
Expected: `CREATE FUNCTION` / `CREATE TRIGGER` (idempotent; safe to re-run).

- [ ] **Step 4: Recreate the container and confirm the daemon + script**

```bash
docker rm -f mail-server && # docker run / compose up with the new image
docker logs mail-server 2>&1 | grep sieve-forward-sync   # "connected; listening" + "regenerated ... row(s)"
docker exec mail-server sh -c 'cat /var/lib/dovecot/sieve/forward.sieve'
docker exec mail-server doveconf -n 2>/dev/null | grep -i sieve_before
```
Expected: daemon connected; script lists a block per mailboxed forward; `sieve_before` present. If `doveconf` errors on the plugin block, switch `95-sieve.conf.tpl` to the 2.4 `sieve_script before { path = /var/lib/dovecot/sieve/forward.sieve }` form, rebuild, recheck.

- [ ] **Step 5: LISTEN/NOTIFY liveness**

```bash
# add a forwarding row via mail-controller (or SQL), then within ~1s:
docker logs --tail=5 mail-server 2>&1 | grep "regenerated"
```
Expected: a fresh "regenerated" line appears right after the row change (not only on the 60s fallback).

- [ ] **Step 6: Ham forwards, spam does not**

Send a clean external test message to a mailboxed forward (e.g. `karol@siedlaczek.com.pl` → Gmail) and confirm delivery to Gmail with the local copy behaviour matching `keep_copy`. Then send a GTUBE test (`XJS*C4JDBQADN1.NSBN3*2IDNEN*GTUBE-STANDARD-ANTI-UBE-TEST-EMAIL*C.34X`) and confirm it is **not** forwarded (stays local / Junk), by checking:
```bash
docker logs mail-server 2>&1 | grep -E "X-Spam|redirect|to=<.*gmail"
```
Expected: clean mail shows an outbound `to=<...gmail...> status=sent`; GTUBE shows `X-Spam` add-header and **no** outbound relay for it.

- [ ] **Step 7: Commit any syntax adjustment from Step 4, then merge**

```bash
git add -A && git commit -m "test: verify Sieve ham-forwarding end-to-end" || true
```

---

## Rollout / rollback notes

- **Order matters:** apply the schema trigger (Step 3) before or with the new image. Without the trigger the daemon still works — it resyncs every `SIEVE_SYNC_INTERVAL` (60s) — just without instant updates.
- **mail-controller:** unchanged. It keeps writing `forwardings`; the trigger + daemon pick up changes. No API/schema change on its side beyond the shared `forwardings` table gaining a trigger.
- **Rollback:** revert `virtual_alias_maps.cf.tpl` (Task 2) to restore Postfix-level redirect, and remove `95-sieve.conf` / the `sieve-forward-sync` service. The trigger is harmless to leave in place.
- **Scope boundary:** non-mailbox (pure alias) forwards still redirect at Postfix **unfiltered** — spam-gating only covers addresses that are real mailboxes. If a pure-alias address must be filtered too, give it a mailbox so it flows through Sieve.

## Self-review notes (author)

- Spec coverage: forwardings-in-psql ✅ (Tasks 1,2,5), mail-controller unchanged ✅ (Task 1 trigger only), spam gate ✅ (Tasks 3,4), LISTEN/NOTIFY ✅ (Tasks 1,5), deliver-locally-then-Sieve ✅ (Task 2,7).
- Type consistency: `build_sieve(rows)`/`write_script(text,out)`/`regenerate(conn)`/`fetch_rows(conn)` names match across Tasks 4–5 and tests.
- Open verification (not a placeholder — a real in-container check): exact Pigeonhole 2.4.1 `sieve_before` syntax is confirmed in Task 7 Step 5 / Task 8 Step 4, with the documented fallback form.
