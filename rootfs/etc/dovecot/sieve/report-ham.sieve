# imap_sieve: runs when a message is moved/copied OUT of Junk. Teaches it as ham
# — but ONLY when it lands in a real folder, NOT when it is deleted (moved to
# Trash). "Empty the Junk folder" (Junk -> Trash) must not train quarantined spam
# as ham and poison Bayes. `imap.mailbox` is the destination mailbox (environment
# extension); the guard skips the learn when that destination is Trash.
require ["vnd.dovecot.pipe", "environment"];
if not environment :is "imap.mailbox" "Trash" {
  pipe "rspamd-learn-ham.sh";
}
