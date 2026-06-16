# Mail storage. Format is Maildir, rooted at each mailbox's home — the home path
# is returned by the SQL userdb (`/var/vmail/<domain>/<localpart>`), so the
# effective store is `/var/vmail/<domain>/<localpart>/Maildir`.
# mail_location = maildir:~/Maildir
mail_driver = maildir
mail_path = ~/Maildir

# Single system identity for every virtual mailbox (created in the image).
# Pinning first/last_valid_uid to 5000 guarantees Dovecot never delivers as
# root or any other system account even if userdb returned a bad uid.
mail_uid = 5000
mail_gid = 5000
first_valid_uid = 5000
last_valid_uid = 5000

# Standard special-use mailboxes auto-created on first login.
namespace inbox {
  inbox = yes
  mailbox Drafts { special_use = \Drafts }
  mailbox Junk   { special_use = \Junk }
  mailbox Sent   { special_use = \Sent }
  mailbox Trash  { special_use = \Trash }
}
