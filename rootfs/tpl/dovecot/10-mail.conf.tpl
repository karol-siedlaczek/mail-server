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

# Standard special-use mailboxes. `auto = subscribe` both CREATES the folder and
# SUBSCRIBES it on the user's first mailbox access, so it exists server-side and
# shows up in clients that only list subscribed folders (SnappyMail). Without it
# the Junk folder never exists and the spam-gating `fileinto :create "Junk"` in
# the forward script had to create it on the fly; here we also give Sent/Drafts/
# Trash/Archive so they populate the webmail system-folder mapping.
namespace inbox {
  inbox = yes
  mailbox Drafts {
    special_use = \Drafts
    auto = subscribe
  }
  mailbox Junk {
    special_use = \Junk
    auto = subscribe
  }
  mailbox Sent {
    special_use = \Sent
    auto = subscribe
  }
  mailbox Trash {
    special_use = \Trash
    auto = subscribe
  }
  mailbox Archive {
    special_use = \Archive
    auto = subscribe
  }
}
