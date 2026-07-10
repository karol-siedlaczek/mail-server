# Fuzzy hashing — recognises a message as "the same as spam seen before" by
# fuzzy (edit-tolerant) content fingerprints, catching re-sends of a campaign
# even when names/amounts are swapped.
#
# The PUBLIC rspamd.com feed is rspamd's stock read-only `rule "fuzzy"` (its
# correct encryption key ships in the base fuzzy_check.conf) and is left
# UNTOUCHED — merging a rule of that name here risks clobbering the working key.
# This file only ADDS a second, private rule backed by our own fuzzy_storage
# worker (worker-fuzzy.inc), so the mail-learn-spam helper can teach the appliance
# campaign fingerprints that never reach the global feed. Because both rules map
# flag 1 → FUZZY_DENIED, local hits carry the same (well-scored) symbol as public
# ones — no new symbol score to define.
rule "local" {
  # Our own fuzzy_storage worker on loopback (see worker-fuzzy.inc). Writable, so
  # `rspamc fuzzy_add` (mail-learn-spam) can store hashes here.
  servers = "127.0.0.1:11335";
  read_only = false;
  # Loopback to our own worker → no transport encryption needed.
  encryption = false;
  # Learn under flag 1 (spam/deny); inbound hits raise FUZZY_DENIED like the feed.
  fuzzy_map = {
    FUZZY_DENIED {
      max_score = 20.0;
      flag = 1;
    }
  }
}
