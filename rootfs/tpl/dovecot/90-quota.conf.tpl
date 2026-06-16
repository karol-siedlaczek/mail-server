# Per-mailbox quota. The `count` backend derives current usage from Dovecot's
# own index (no fragile dovecot-uidlist quota file to keep in sync). The LIMIT
# is not hardcoded here: the SQL userdb returns `quota_storage_size` per user
# (NULL = unlimited when quota_bytes is 0), and Dovecot 2.4 applies that as the
# storage quota automatically.
mail_plugins {
  quota = yes
}

quota_storage_size_user = yes

quota count {
  quota_driver = count
}

# Make the count backend authoritative for the storage dimension that the
# userdb's quota_storage_size feeds.
protocol imap {
  mail_plugins {
    imap_quota = yes
  }
}
