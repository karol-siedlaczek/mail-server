# Backlog: making spam false-positives visible (forward-to-Gmail workflow)

**Status:** idea / not yet designed. Revisit later (brainstorm → spec → plan → TDD).
**Date:** 2026-07-11

## Problem

The spam-gated forward files `X-Spam: Yes` mail into the local `Junk` mailbox and
**never forwards it**. Consequence: a false positive (legit mail wrongly flagged)
never reaches the Gmail account the user actually reads — it is silently
quarantined locally and easy to miss. The user reads mail on Gmail, so they need
a way to notice/correct FPs without a hard requirement to poll the local box.

This is the accepted tradeoff of "never relay spam" (protects sending
reputation). These options close the visibility loop.

## Options (not mutually exclusive)

### 1. Local mailbox access (webmail / IMAP client)
Review `Junk` directly via a webmail (SnappyMail / Roundcube) or `doveadm`.
On a false positive → `mail-release`. Full control, but relies on the user
remembering to look. Best paired with server-side `imap_sieve` learn-on-move so
"mark as junk / move out of junk" in any client trains rspamd automatically
(this was a deliberate non-goal originally; revisit if a local client is added).

### 2. Quarantine digest to Gmail (recommended)
A periodic (e.g. daily) cron/script scans the local `Junk` via `doveadm` and
emails a **summary** to Gmail: From / Subject of quarantined messages
(optionally the `.eml` attached). Does NOT forward the spam itself → no
reputation hit — but gives the user a Gmail-side heads-up so they only dig into
the local box when something looks like a FP. Fits the Gmail-centric workflow.

### 3. Tiered gate (recommended, complements #2)
Instead of a binary cut at `add_header` (6):
- score ≥ ~10 (confident spam) → `Junk` local, not forwarded
- 6 ≤ score < ~10 (suspicious) → **forward to Gmail with a `[SPAM?]` subject tag**
- score < 6 → forward clean

Borderline mail (where FPs live) stays visible in Gmail, tagged; only
high-confidence spam is quarantined locally. Smallest "invisible FP" risk, minor
reputation cost (low volume of tagged suspicious mail). Requires teaching the
Sieve generator / rspamd a second threshold.

## Direct answer captured
To tell rspamd "this isn't spam" you must feed the message to `learn_ham`, and
that message lives in the local `Junk` — so some access to the local mailbox
(GUI, `doveadm`, or a digest that attaches the `.eml`) is unavoidable. A local
client is the most ergonomic form, not the only one.

## Recommendation to revisit
Primary: **#2 digest** and/or **#3 tiered gate** (keep the user in Gmail).
Secondary: **#1 lightweight webmail** (SnappyMail/Roundcube) for occasional
hands-on triage, + `imap_sieve` learn-on-move if a local client is adopted.

## Related
- Sieve gate + Junk filing: `docs/superpowers/plans/2026-07-01-sieve-ham-forwarding.md`,
  `rootfs/usr/local/bin/sieve-forward-sync` (`build_sieve`)
- Learning: `docs/superpowers/specs/2026-07-10-bayes-fuzzy-spam-learning-design.md`,
  helpers `mail-learn-spam` / `mail-learn-ham` / `mail-release`
- Webmail client evaluation (SnappyMail / Roundcube / SOGo) — see conversation
  2026-07-11; SOGo needs its own DB + auth source wired to the Postgres users.
