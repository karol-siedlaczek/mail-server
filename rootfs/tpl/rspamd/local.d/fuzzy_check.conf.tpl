# Fuzzy hashing — recognises a message as "the same as spam seen before" by
# fuzzy (edit-tolerant) content fingerprints, catching re-sends of a campaign
# even when names/amounts are swapped.
#
# The PUBLIC rspamd.com feed is rspamd's stock read-only `rule "rspamd.com"` (its
# correct encryption key ships in modules.d/fuzzy_check.conf) and is left
# UNTOUCHED — merging a rule of that name here risks clobbering the working key.
# This file only ADDS a second, private rule backed by our own fuzzy_storage
# worker (worker-fuzzy.inc), so the mail-learn-spam helper can teach the appliance
# campaign fingerprints that never reach the global feed.
rule "local" {
  # mumhash matches the stock rspamd.com rule and the fingerprints written by
  # mail-learn-spam via `rspamc fuzzy_add`.
  algorithm = "mumhash";
  # Our own fuzzy_storage worker on loopback (see worker-fuzzy.inc). Writable, so
  # fuzzy_add can store hashes here; loopback needs no transport encryption.
  servers = "127.0.0.1:11335";
  read_only = false;
  # A DISTINCT symbol — reusing the stock FUZZY_DENIED triggers a duplicate-symbol
  # warning and would let this rule's map clobber the feed's. Its score is
  # registered in fuzzy_group.conf; flag 1 = the spam/deny bucket mail-learn-spam
  # writes to.
  fuzzy_map = {
    LOCAL_FUZZY_DENIED {
      hits_limit = 20.0;
      flag = 1;
    }
  }
}
