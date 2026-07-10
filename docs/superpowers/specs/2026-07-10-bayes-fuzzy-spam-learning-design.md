# Bayes autolearn + fuzzy_check + spam review/release — Design

**Date:** 2026-07-10
**Status:** Approved (design)

## Problem

The server forwards mailbox mail to external addresses (e.g. Gmail) via a
spam-gated Sieve `redirect`: it forwards only when rspamd did **not** flag the
message (`X-Spam: Yes` absent — see `2026-07-01-sieve-ham-forwarding`). The gate
works, but rspamd itself under-detects "clean" spam: a technically compliant
bulk sender (SPF/DKIM/DMARC all pass, no blacklist/URL hits) scores far below
the `add_header = 6` threshold. A real example scored **−0.70 / 15** and was
forwarded.

Consequences observed in production logs:
- Spam reaches the user's Gmail.
- Gmail rate-limits the whole server: `421-4.7.28 ... unusual rate of
  unsolicited mail ... temporarily rate limited`, deferring even legitimate
  forwards (including DMARC reports). This is a reputation problem, not just an
  inbox-noise problem.

**Root insight:** rspamd has no innate knowledge that a compliant sender's bulk
mail is unwanted. That signal must be *taught* (Bayes + fuzzy) or arrive from
global reputation feeds. Crucially, **autolearn alone will never catch this
class** — autolearn-spam fires only at a very high score (≥12), and this mail
scored −0.70. The long tail of clean spam must be seeded by hand.

## Goal

Raise rspamd's detection of unwanted mail so similar messages cross the `6`
threshold (get `X-Spam: Yes`) and are therefore **not** forwarded — protecting
the server's sending reputation. Provide a clean local review queue and a manual
release path for false positives. Modest, probabilistic target: reduce the
forwarded-spam rate, not eliminate it with certainty.

## Non-goals

- Not changing the forwarding source of truth (`forwardings` in Postgres) or
  `mail-controller`.
- Not lowering `add_header` below 6 (raises false positives and, worse in this
  model, silently *suppresses* legitimate forwards).
- Not building a full quarantine UI. Review is via IMAP into the Junk folder.
- Not learning-on-Junk-move via imap_sieve (user reads mail on Gmail; the local
  Junk signal is only exercised during false-positive review, handled by the
  `mail-release` helper instead).

## Design decisions (confirmed)

- **Bayes learning:** autolearn by conservative thresholds **plus** manual
  training. Autolearn `[-2, 12]` (learn ham when score < −2, spam when > 12).
- **Fuzzy:** public `rspamd.com` feed (read-only) **plus** a local read-write
  fuzzy storage in Redis.
- **Training feed:** CLI helpers on the host (`ssh worker-01 docker exec ...`),
  not a learn-folder daemon and not imap_sieve. Bootstrap uses the same helper.

## Architecture / components

Rspamd runs as a single process: the milter proxy worker in `self_scan` mode
(`worker-proxy.inc.tpl`). Redis is already wired for all stateful modules
(`redis.conf.tpl`, per-module prefixes; Bayes prefix `_bayes`). Every
`rootfs/tpl/rspamd/local.d/*.tpl` is auto-rendered to `/etc/rspamd/local.d/` by
`render-config.sh` (no `render.map` entry needed).

### Section 1 — Bayes classifier

**Create:** `rootfs/tpl/rspamd/local.d/classifier-bayes.conf.tpl`

- `autolearn = [-2, 12];` — conservative; the sample mail (−0.70) sits in the
  neutral band and is never auto-learned either way.
- `min_learns` lowered from the stock 200 to ~50 so Bayes activates on a
  low-volume server (below the threshold Bayes emits no `BAYES_*` symbols).
- Shared statistics (not per-user) so all learning pools into one model.
- Redis backend inherited from the global `redis.conf` (prefix `_bayes`).

**Expectation:** Bayes is the slow-burn generalizer. It emits symbols only after
~`min_learns` samples per class; day-1 impact is negligible.

### Section 2 — fuzzy_check + local fuzzy storage

**Create:**
- `rootfs/tpl/rspamd/local.d/fuzzy_check.conf.tpl`
  - Rule `rspamd_com`: `servers = "rspamd.com:11335"`, `read_only = true`, stock
    encryption key, `FUZZY_DENIED` map — immediate global-campaign coverage.
  - Rule `local`: `servers = "127.0.0.1:11335"`, `read_only = false`, own
    `LOCAL_FUZZY_DENIED` flag/symbol — fed by the helpers.
- `rootfs/tpl/rspamd/local.d/worker-fuzzy.inc.tpl`
  - `bind_socket = "127.0.0.1:11335"`, `backend = "redis"` (global servers),
    `count = 1`, sensible `expire` (e.g. 90d). rspamd master spawns it; no new
    s6 service.

**Expectation:** fuzzy is the immediate win — one `fuzzy_add` catches
near-duplicate re-sends of a campaign at once.

### Section 3 — Spam → Junk review queue (Sieve generator change)

**Modify:** `rootfs/usr/local/bin/sieve-forward-sync` (`build_sieve`)

Today, a spam-flagged message with `keep_copy=false` falls through to default
LMTP delivery into **INBOX** (not lost, but mixed and unreviewable-as-a-queue).
Restructure each per-source block so spam is filed into the existing `Junk`
mailbox (`10-mail.conf.tpl` defines `special_use \Junk`) regardless of
`keep_copy`:

```sieve
require ["envelope", "copy", "fileinto"];
if envelope :is "to" "<source>" {
  if header :contains "X-Spam" "Yes" { fileinto "Junk"; stop; }
  redirect [:copy] "<dest>";   # :copy only when keep_copy
  [stop;]                       # stop only when not keep_copy
}
```

- Add `fileinto` to the `require` list.
- Ham path unchanged (redirect + copy/stop per `keep_copy`).
- Spam path: `fileinto "Junk"; stop;` — a clean review queue readable via IMAP
  on the mailbox directly (not via Gmail).
- `build_sieve` is a pure function → covered by unit tests
  (`tests/test_sieve_forward.py`): spam→Junk block present, `fileinto` required,
  ham path preserved for both keep/no-keep, multi-destination, escaping.

### Section 4 — CLI helpers (bundled in the image)

**Create under `rootfs/usr/local/bin/`:**
- `mail-learn-spam` — `rspamc learn_spam` + `rspamc fuzzy_add` (local rule flag)
  on an `.eml` (stdin or path). Used for bootstrap and ongoing training.
- `mail-learn-ham` — `rspamc learn_ham` for a false positive found in Junk.
- `mail-release` — manual release of a false positive: re-inject the `.eml`
  **directly to the destination address** (bypassing the local Sieve so it does
  not loop back into Junk), then `rspamc learn_ham`. Destination taken from the
  `forwardings` table for the recipient, or an explicit argument.

All reach the rspamd controller (already bound with a password,
`render-config.sh:575`); helpers source that password from the container
environment / rendered config rather than hardcoding it.

### Section 5 — Bootstrap, tests, verification

- **Bootstrap:** run `mail-learn-spam` on the sample credit-spam message against
  the live server. Spam is **not** committed to the repo.
- **Render tests** (`tests/`): each new template renders with no unresolved
  `${...}` and contains expected keys (autolearn array, fuzzy rules, fuzzy
  worker bind, `fileinto`/Junk in the generated Sieve).
- **Unit tests:** `build_sieve` spam→Junk behavior (Section 3).
- **In-container e2e:** `rspamc stat` shows the classifier; re-scanning the
  bootstrapped sample (or a near-duplicate) now scores ≥ 6 → `X-Spam: Yes` →
  filed to Junk, **not** relayed to Gmail; a clean control message still
  forwards.

### Section 6 — README documentation

**Modify:** `README.md` — add a subsection under `## Operations` (alongside
`### DKIM keys`, `### DNS resolver`, `### Reading a mailbox`), e.g.
`### Spam filtering & training`, documenting:
- How Bayes autolearn + fuzzy work here and their thresholds.
- The `mail-learn-spam`, `mail-learn-ham`, `mail-release` helpers with example
  `docker exec` / `ssh worker-01` invocations.
- The Junk review queue: where spam-flagged mail lands and how to review it over
  IMAP, and how to release a false positive.

## Data flow

```
inbound mail → rspamd (proxy self_scan)
   ├─ Bayes (learned tokens) + fuzzy (public + local) contribute to score
   ├─ score ≥ 6 → milter adds X-Spam: Yes
   └─ LMTP → Dovecot → forward.sieve:
        X-Spam: Yes  → fileinto Junk; stop      (stays on server, reviewable)
        otherwise    → redirect [:copy] dest    (forwarded)

operator (ssh worker-01 docker exec):
   mail-learn-spam <eml>  → rspamc learn_spam + fuzzy_add   (Bayes + local fuzzy)
   mail-learn-ham  <eml>  → rspamc learn_ham
   mail-release    <eml>  → inject direct to dest (bypass sieve) + learn_ham
```

## Risks / mitigations

- **Bayes poisoning:** conservative autolearn band `[-2, 12]`; the sample mail is
  outside it. Manual training is the primary early signal.
- **Bayes latency:** `min_learns` lowered so symbols activate sooner; fuzzy
  carries the immediate wins meanwhile.
- **Release loop:** `mail-release` injects to the external destination directly,
  never re-entering the recipient's Sieve.
- **Reputation recovery:** the Gmail 421 is temporary and clears once spam stops
  flowing; reducing forwarded spam is the actual fix.

## Rollback

Remove the new `local.d` templates and helper scripts; revert `build_sieve` to
the pre-Junk form. Redis Bayes/fuzzy data is harmless to leave.
