# imap_sieve learn-on-move — progress ledger (NO COMMITS: user reviews working tree)
Plan: docs/superpowers/plans/2026-07-13-imapsieve-learn-on-move.md

Task 1: complete (controller secure_ip; render-config.sh + test_rspamd_render.py; rspamd suite 18/18; NOT committed)
Task 2: complete (learn wrappers + Dockerfile chmod + test_imapsieve_learn.py; 3/3; NOT committed). NOTE: Dockerfile is at repo root, not rootfs/ (plan typo).
Task 3: complete (report-spam/ham.sieve + tests; 5/5 in test_imapsieve_learn.py; NOT committed)
Task 4: complete (96-imapsieve.conf.tpl + render.map + dovecot render tests; full suite 133 passed; NOT committed)
Task 5: config-level verification COMPLETE — corrected imapsieve syntax to Dovecot 2.4.1 block form
  (mailbox Junk { sieve_script report-spam { type=before; cause=copy; path=... }} + imapsieve_from Junk {...}).
  2.3 numbered imapsieve_mailbox1_* was REJECTED by doveconf; fixed template + render tests.
  doveconf -n PARSED OK on rendered config; wrappers/scripts shipped; full suite 133 passed. NOT committed.
  DEFERRED: live doveadm-move learn test (needs deploying this image; prod runs v1.0.2).

Final review fixes applied (verified in-container, NOT committed):
- #1 report-ham.sieve: guard `if not environment :is "imap.mailbox" "Trash"` — Junk->Trash no longer trains ham (was Bayes-poisoning). sievec -c compiles it.
- #2 96-imapsieve.conf.tpl: spam rule `cause = copy append` (catches direct APPEND into Junk). doveconf accepts.
- #4 test_dovecot_render.py: 96-imapsieve.conf.tpl added to DOVECOT_CONFD_TEMPLATES (CI doveconf -n now validates it).
- minor: wrappers drop exec, add `-t 10` + `|| true` (bounded wait, swallow already-learned/down).
- #3 Dockerfile: chown vmail:vmail /etc/dovecot/sieve (Dovecot caches compiled .svbin; no per-move recompile).
Gates: full suite 134 passed; doveconf -n OK; sievec -c both report scripts OK.
