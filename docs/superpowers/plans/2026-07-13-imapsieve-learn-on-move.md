# IMAP Sieve learn-on-move (GUI-driven Bayes training) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make moving a message **into** the `Junk` mailbox teach rspamd Bayes it is spam, and moving it **out of** `Junk` teach it ham — so a webmail (SnappyMail) or any IMAP client trains the classifier just by filing mail.

**Architecture:** Enable Dovecot Pigeonhole's `imap_sieve` for the IMAP protocol. Two global Sieve scripts (triggered on COPY/APPEND into `Junk`, and COPY out of `Junk`) `pipe` the message through `sieve_extprograms` to a tiny wrapper that runs `rspamc learn_spam` / `learn_ham`. The rspamd controller is made to trust `127.0.0.1` (`secure_ip`), so the local wrapper learns without a password even though the controller is password-protected for the HAProxy path.

**Tech Stack:** Dovecot 2.4 + Pigeonhole (`dovecot-sieve`: `imap_sieve`, `sieve_extprograms`), Rspamd 3.11 (`rspamc`), bash `render-config.sh`, pytest render tests, s6-overlay v3.

## Global Constraints

- This is a **separate change** from the existing delivery-time forward script (`sieve-forward-sync` → `sieve_before` `forward.sieve`); do not modify that mechanism.
- Learn-on-move does **Bayes only** (`learn_spam`/`learn_ham`) — NOT `fuzzy_add`. Fuzzy fingerprinting stays manual (`mail-learn-spam`) to avoid fingerprinting every message a user junks.
- Templates render through `render-config.sh`; every `${VAR}` used must already be in `DUMP_VARS`. This plan adds no new env var.
- Unit/render tests must stay green: `python3 -m pytest tests/ -k "not itest and not integration"`.
- Dovecot 2.4 removed the `plugin { }` block; sieve settings are top-level (see `95-sieve.conf.tpl`). Exact Pigeonhole 2.4 `imapsieve_*` spelling is confirmed in-container in Task 5, with a documented fallback.
- rspamd controller: local (`127.0.0.1`) requests must be able to run enable-level commands (`learn_*`) without a password; remote (HAProxy) still requires it.
- Follow existing file/comment style. Commit after every task.

---

## File Structure

**Created:**
- `rootfs/tpl/dovecot/96-imapsieve.conf.tpl` — enables `imap_sieve` for IMAP + the `imapsieve_mailbox*` rules, `sieve_plugins`, `sieve_pipe_bin_dir`. Renders to `/etc/dovecot/conf.d/96-imapsieve.conf`.
- `rootfs/etc/dovecot/sieve/report-spam.sieve` — global script: `pipe` message to the learn-spam wrapper.
- `rootfs/etc/dovecot/sieve/report-ham.sieve` — global script: `pipe` to the learn-ham wrapper.
- `rootfs/usr/lib/dovecot/sieve/rspamd-learn-spam.sh` — wrapper: `rspamc learn_spam`.
- `rootfs/usr/lib/dovecot/sieve/rspamd-learn-ham.sh` — wrapper: `rspamc learn_ham`.
- `tests/test_imapsieve_learn.py` — asserts wrapper/script files exist, are executable, and call the right `rspamc` subcommand.

**Modified:**
- `rootfs/usr/local/bin/render-config.sh` — add `secure_ip` (127.0.0.1, ::1) to the controller block.
- `rootfs/tpl/render.map` — map the new `96-imapsieve.conf` template.
- `rootfs/Dockerfile` — `chmod +x` the two sieve wrappers.
- `tests/test_rspamd_render.py` — assert `secure_ip` in the exposed-controller render.
- `tests/test_dovecot_render.py` — render assertions for `96-imapsieve.conf`.

---

## Task 1: rspamd controller trusts localhost (`secure_ip`)

So the Sieve wrapper can run `rspamc learn_*` on `127.0.0.1:11334` without a password, while the HAProxy-facing path stays password-protected.

**Files:**
- Modify: `rootfs/usr/local/bin/render-config.sh` (controller block, ~line 569-575)
- Test: `tests/test_rspamd_render.py`

**Interfaces:**
- Produces: rendered `worker-controller.inc` containing `secure_ip = "127.0.0.1";` and `secure_ip = "::1";` whenever the controller is password-exposed. Consumed logically by the wrappers in Task 2.

- [ ] **Step 1: Add the failing assertion to the existing exposed-controller test**

In `tests/test_rspamd_render.py`, inside `test_controller_exposed_with_password`, after the existing `enable_password` assertion add:

```python
    # Local (loopback) requests are trusted for enable-level commands (learn_*),
    # so the Sieve learn-on-move wrapper needs no password; remote still does.
    assert 'secure_ip = "127.0.0.1";' in t
    assert 'secure_ip = "::1";' in t
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_rspamd_render.py::test_controller_exposed_with_password -v`
Expected: FAIL (`secure_ip` not found).

- [ ] **Step 3: Emit `secure_ip` in the controller block**

In `rootfs/usr/local/bin/render-config.sh`, change the exposed-controller `printf` group (currently emitting `bind_socket`/`password`/`enable_password`) to:

```sh
        {
            printf 'bind_socket = "*:11334";\n'
            printf 'password = "%s";\n' "$_chash"
            printf 'enable_password = "%s";\n' "$_chash"
            # Trust loopback for enable-level commands (Sieve learn-on-move runs
            # `rspamc learn_*` locally); HAProxy/remote still needs the password.
            printf 'secure_ip = "127.0.0.1";\n'
            printf 'secure_ip = "::1";\n'
        } > "$_ctrl"
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_rspamd_render.py::test_controller_exposed_with_password -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rootfs/usr/local/bin/render-config.sh tests/test_rspamd_render.py
git commit -m "feat(rspamd): trust loopback (secure_ip) on the controller for local learns"
```

---

## Task 2: rspamc learn wrapper scripts

Tiny programs that `sieve_extprograms` runs with the message on stdin.

**Files:**
- Create: `rootfs/usr/lib/dovecot/sieve/rspamd-learn-spam.sh`
- Create: `rootfs/usr/lib/dovecot/sieve/rspamd-learn-ham.sh`
- Modify: `rootfs/Dockerfile` (chmod +x the wrappers)
- Test: `tests/test_imapsieve_learn.py`

**Interfaces:**
- Produces: executables `/usr/lib/dovecot/sieve/rspamd-learn-spam.sh` and `…-ham.sh`, each reading a message on stdin and calling `/usr/bin/rspamc learn_spam` / `learn_ham`. Referenced by the Sieve scripts in Task 3 (by basename) and the config in Task 4 (`sieve_pipe_bin_dir`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_imapsieve_learn.py
import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "rootfs" / "usr" / "lib" / "dovecot" / "sieve"

def _read(name):
    return (BIN / name).read_text()

def test_learn_spam_wrapper_calls_rspamc():
    assert (BIN / "rspamd-learn-spam.sh").is_file()
    t = _read("rspamd-learn-spam.sh")
    assert t.startswith("#!/bin/sh")
    assert "/usr/bin/rspamc learn_spam" in t

def test_learn_ham_wrapper_calls_rspamc():
    assert (BIN / "rspamd-learn-ham.sh").is_file()
    t = _read("rspamd-learn-ham.sh")
    assert "/usr/bin/rspamc learn_ham" in t

def test_wrappers_are_executable_in_git():
    for name in ("rspamd-learn-spam.sh", "rspamd-learn-ham.sh"):
        mode = os.stat(BIN / name).st_mode
        assert mode & stat.S_IXUSR, f"{name} not executable"
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_imapsieve_learn.py -v`
Expected: FAIL (files missing).

- [ ] **Step 3: Create the wrappers**

`rootfs/usr/lib/dovecot/sieve/rspamd-learn-spam.sh`:

```sh
#!/bin/sh
# Called by Pigeonhole imap_sieve (sieve_extprograms) with the message on stdin
# when a user moves/copies mail INTO Junk. Learns it as SPAM in Bayes. The rspamd
# controller trusts loopback (secure_ip), so no password is needed here.
exec /usr/bin/rspamc learn_spam
```

`rootfs/usr/lib/dovecot/sieve/rspamd-learn-ham.sh`:

```sh
#!/bin/sh
# Called by imap_sieve when a user moves mail OUT of Junk. Learns it as HAM.
exec /usr/bin/rspamc learn_ham
```

- [ ] **Step 4: Mark them executable (tracked in git) + chmod at build**

Run: `chmod +x rootfs/usr/lib/dovecot/sieve/rspamd-learn-spam.sh rootfs/usr/lib/dovecot/sieve/rspamd-learn-ham.sh`

In `rootfs/Dockerfile`, extend the existing chmod line (the one doing `chmod +x /usr/local/bin/*`) to also cover the sieve wrappers, e.g.:

```dockerfile
    chmod +x /usr/local/bin/*.sh /usr/local/bin/* 2>/dev/null || true; \
    chmod +x /usr/lib/dovecot/sieve/*.sh 2>/dev/null || true; \
```

- [ ] **Step 5: Run test, expect PASS**

Run: `python3 -m pytest tests/test_imapsieve_learn.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add rootfs/usr/lib/dovecot/sieve/rspamd-learn-spam.sh rootfs/usr/lib/dovecot/sieve/rspamd-learn-ham.sh rootfs/Dockerfile tests/test_imapsieve_learn.py
git commit -m "feat(dovecot): rspamc learn wrappers for imap_sieve learn-on-move"
```

---

## Task 3: Global report Sieve scripts

The scripts imap_sieve runs before the folder action; they pipe the message to the wrappers.

**Files:**
- Create: `rootfs/etc/dovecot/sieve/report-spam.sieve`
- Create: `rootfs/etc/dovecot/sieve/report-ham.sieve`
- Test: `tests/test_imapsieve_learn.py` (extend)

**Interfaces:**
- Consumes: wrapper basenames from Task 2 (resolved via `sieve_pipe_bin_dir`, Task 4).
- Produces: `/etc/dovecot/sieve/report-spam.sieve` and `report-ham.sieve`, referenced by `imapsieve_mailbox*_before = file:…` in Task 4.

- [ ] **Step 1: Write the failing test** (append to `tests/test_imapsieve_learn.py`)

```python
SIEVE = REPO / "rootfs" / "etc" / "dovecot" / "sieve"

def test_report_spam_sieve_pipes_to_wrapper():
    t = (SIEVE / "report-spam.sieve").read_text()
    assert 'require ["vnd.dovecot.pipe"];' in t
    assert 'pipe "rspamd-learn-spam.sh";' in t

def test_report_ham_sieve_pipes_to_wrapper():
    t = (SIEVE / "report-ham.sieve").read_text()
    assert 'require ["vnd.dovecot.pipe"];' in t
    assert 'pipe "rspamd-learn-ham.sh";' in t
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `python3 -m pytest tests/test_imapsieve_learn.py -k report -v`
Expected: FAIL (files missing).

- [ ] **Step 3: Create the scripts**

`rootfs/etc/dovecot/sieve/report-spam.sieve`:

```
# imap_sieve: runs when a message is copied/appended INTO Junk. Pipes the message
# to the learn-spam wrapper (sieve_pipe_bin_dir). Bayes only — no fuzzy.
require ["vnd.dovecot.pipe"];
pipe "rspamd-learn-spam.sh";
```

`rootfs/etc/dovecot/sieve/report-ham.sieve`:

```
# imap_sieve: runs when a message is moved OUT of Junk. Pipes to the learn-ham
# wrapper so a false positive the user rescues is taught as ham.
require ["vnd.dovecot.pipe"];
pipe "rspamd-learn-ham.sh";
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_imapsieve_learn.py -k report -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rootfs/etc/dovecot/sieve/report-spam.sieve rootfs/etc/dovecot/sieve/report-ham.sieve tests/test_imapsieve_learn.py
git commit -m "feat(dovecot): global report-spam/ham sieve scripts for imap_sieve"
```

---

## Task 4: Dovecot `96-imapsieve.conf` template + wiring

**Files:**
- Create: `rootfs/tpl/dovecot/96-imapsieve.conf.tpl`
- Modify: `rootfs/tpl/render.map`
- Test: `tests/test_dovecot_render.py`

**Interfaces:**
- Consumes: `report-spam.sieve`/`report-ham.sieve` (Task 3), wrappers via `sieve_pipe_bin_dir` (Task 2).
- Produces: `/etc/dovecot/conf.d/96-imapsieve.conf` enabling `imap_sieve` for IMAP and the two mailbox rules.

- [ ] **Step 1: Write the failing render tests** (append to `tests/test_dovecot_render.py`)

```python
def test_imapsieve_enables_imap_sieve(render_dovecot):
    out = render_dovecot("96-imapsieve.conf.tpl")
    # imap_sieve enabled for the IMAP protocol (2.4 per-protocol mail_plugins block)
    assert "protocol imap {" in out
    assert "imap_sieve = yes" in out
    assert "sieve_plugins = sieve_imapsieve sieve_extprograms" in out
    assert "sieve_pipe_bin_dir = /usr/lib/dovecot/sieve" in out

def test_imapsieve_junk_rules(render_dovecot):
    out = render_dovecot("96-imapsieve.conf.tpl")
    # INTO Junk -> learn spam
    assert "imapsieve_mailbox1_name = Junk" in out
    assert "imapsieve_mailbox1_causes = COPY APPEND" in out
    assert "imapsieve_mailbox1_before = file:/etc/dovecot/sieve/report-spam.sieve" in out
    # OUT OF Junk -> learn ham
    assert "imapsieve_mailbox2_from = Junk" in out
    assert "imapsieve_mailbox2_before = file:/etc/dovecot/sieve/report-ham.sieve" in out

def test_render_map_has_imapsieve_conf():
    rm = (REPO / "rootfs" / "tpl" / "render.map").read_text()
    assert "tpl/dovecot/96-imapsieve.conf.tpl" in rm
    assert "/etc/dovecot/conf.d/96-imapsieve.conf" in rm
```

(`REPO` is already defined at the top of `test_dovecot_render.py`; if not, add `from pathlib import Path` and `REPO = Path(__file__).resolve().parents[1]`.)

- [ ] **Step 2: Run them, expect FAIL**

Run: `python3 -m pytest tests/test_dovecot_render.py -k imapsieve -v`
Expected: FAIL (template + mapping missing).

- [ ] **Step 3: Create `rootfs/tpl/dovecot/96-imapsieve.conf.tpl`**

```
# Learn-on-move: train rspamd Bayes from IMAP folder actions. Moving/copying a
# message INTO Junk teaches spam; moving it OUT of Junk teaches ham. This is the
# IMAP-time counterpart to the delivery-time forward script (95-sieve.conf) and
# is independent of it. Bayes only — fuzzy stays manual (mail-learn-spam).
#
# Dovecot 2.4: imap_sieve is enabled per-protocol via a mail_plugins block (same
# shape as the lmtp sieve plugin in 20-lmtp.conf); sieve_* settings are top-level.
protocol imap {
  mail_plugins {
    imap_sieve = yes
  }
}

sieve_plugins = sieve_imapsieve sieve_extprograms
sieve_global_extensions = +vnd.dovecot.pipe
sieve_pipe_bin_dir = /usr/lib/dovecot/sieve

# COPY/APPEND into Junk -> report-spam.sieve -> rspamc learn_spam
imapsieve_mailbox1_name = Junk
imapsieve_mailbox1_causes = COPY APPEND
imapsieve_mailbox1_before = file:/etc/dovecot/sieve/report-spam.sieve

# COPY from Junk into any other mailbox -> report-ham.sieve -> rspamc learn_ham
imapsieve_mailbox2_name = *
imapsieve_mailbox2_from = Junk
imapsieve_mailbox2_causes = COPY
imapsieve_mailbox2_before = file:/etc/dovecot/sieve/report-ham.sieve
```

- [ ] **Step 4: Add the mapping to `rootfs/tpl/render.map`** (dovecot group, after the `95-sieve.conf` line)

```
tpl/dovecot/96-imapsieve.conf.tpl               /etc/dovecot/conf.d/96-imapsieve.conf
```

- [ ] **Step 5: Run tests + full dovecot/render suite, expect PASS**

Run: `python3 -m pytest tests/test_dovecot_render.py tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add rootfs/tpl/dovecot/96-imapsieve.conf.tpl rootfs/tpl/render.map tests/test_dovecot_render.py
git commit -m "feat(dovecot): imap_sieve learn-on-move config (Junk in=spam, out=ham)"
```

---

## Task 5: End-to-end verification (build + live) and 2.4 syntax confirmation

**Files:** none (verification only; commit any syntax adjustment it forces). Do not mark complete until every check passes on a running container.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/ -q -k "not itest and not integration"`
Expected: all pass.

- [ ] **Step 2: Build the image**

Run: `docker build -t registry.siedlaczek.com.pl/mail-server:test .`
Expected: build succeeds.

- [ ] **Step 3: `doveconf -n` accepts the config (2.4 syntax gate)**

```bash
docker run --rm --entrypoint bash registry.siedlaczek.com.pl/mail-server:test -c '
  export MAIL_HOSTNAME=mail.example.test PG_HOST=pg PG_PORT=5432 PG_DBNAME=mail \
    PG_USER=ro PG_PASSWORD=x TLS_CERT_FILE=/tls/c.pem TLS_KEY_FILE=/tls/k.pem \
    REDIS_HOST=redis REDIS_PORT=6379 REDIS_DB=0 REDIS_PREFIX=mail REDIS_PASSWORD=y
  /usr/local/bin/render-config.sh >/dev/null 2>&1 || true
  doveconf -n 2>&1 | grep -iE "imap_sieve|imapsieve|sieve_pipe_bin_dir|sieve_plugins" || echo "NONE"
  doveconf 2>&1 | grep -iE "error|unknown setting" | head
'
```
Expected: the imapsieve settings appear and no "unknown setting" errors. **If `doveconf` reports an unknown setting**, the Pigeonhole 2.4 spelling differs — move the `sieve_*`/`imapsieve_*` settings into a `sieve { … }` block or adjust names per the in-container `doveconf` guidance, update `96-imapsieve.conf.tpl` and the Task 4 render assertions to match, rebuild, recheck.

- [ ] **Step 4: Live learn-on-move against a running stack** (compose with Postgres+Redis)

```bash
# with the test image deployed as the mail-server container:
docker exec mail-server sh -c 'rspamc stat | grep -i "messages learned"'   # baseline
# deliver/append a message to a real mailbox, then move it to Junk via IMAP:
docker exec mail-server doveadm mailbox create -u alice@example.test Junk 2>/dev/null || true
docker exec mail-server sh -c 'echo "Subject: imapsieve test\n\nordinary words for tokenizing" \
  | doveadm save -u alice@example.test'
UID=$(docker exec mail-server doveadm search -u alice@example.test mailbox INBOX all | awk "{print \$2}" | tail -1)
docker exec mail-server doveadm move -u alice@example.test Junk mailbox INBOX uid "$UID"
docker exec mail-server sh -c 'rspamc stat | grep -i "messages learned"'    # must increment
docker logs --since 30s mail-server 2>&1 | grep -iE "sieve|pipe|rspamc|learn" | tail
```
Expected: `Messages learned` increments after the move into Junk; moving the message back out of Junk increments the ham statfile. Logs show the pipe/exec firing with no permission or "program not found" error.

- [ ] **Step 5: Commit any syntax adjustment from Step 3, then finish**

```bash
git add -A && git commit -m "test: verify imap_sieve learn-on-move end-to-end" || true
```

---

## Rollout / rollback notes

- **Release:** this repo publishes on a `v*` tag (CI multi-arch build). Ship as the next patch/minor after v1.0.2 once Step 4 passes on the live stack.
- **Rollback:** remove `96-imapsieve.conf` (delete the template + render.map line) and the wrappers/report scripts; the controller `secure_ip` change is harmless to leave.
- **Interaction with webmail:** this is what makes SnappyMail's "mark as spam" / drag-out-of-Junk actually train rspamd. It works with any IMAP client independently of the webmail image.
- **Safety:** learn-on-move is user-driven and Bayes-only; conservative — no fuzzy fingerprints are added, so a mis-click only nudges Bayes token stats (recoverable by moving the message the other way, which learns the opposite class).

## Self-review notes (author)

- Spec coverage (from `docs/superpowers/ideas/2026-07-13-webmail-snappymail-deployment.md`, imap_sieve section): imap_sieve enabled ✅ (Task 4), Junk-in→spam / Junk-out→ham ✅ (Tasks 3-4), wrappers call rspamc reusing local trust ✅ (Tasks 1-2), separate mail-server tag ✅ (rollout).
- Name consistency: wrapper basenames `rspamd-learn-spam.sh` / `rspamd-learn-ham.sh` match across Tasks 2, 3, 4; `sieve_pipe_bin_dir = /usr/lib/dovecot/sieve` matches the wrapper location; `file:/etc/dovecot/sieve/report-*.sieve` matches Task 3 paths.
- Known open verification (not a placeholder — a real in-container check): exact Pigeonhole 2.4 `imapsieve_*`/`sieve_plugins` spelling, confirmed in Task 5 Step 3 with the documented fallback (move into a `sieve {}` block / rename), mirroring how `95-sieve.conf` handled the 2.3→2.4 `sieve_before` change.
