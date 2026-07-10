# Score for the private fuzzy rule's symbol (the "local" rule in
# fuzzy_check.conf). Mirrors the stock FUZZY_DENIED weight (12) so a locally
# taught fingerprint match carries the same push as a public-feed denial —
# enough to cross add_header=6 on its own and file the message to Junk.
symbols = {
  "LOCAL_FUZZY_DENIED" {
    weight = 12.0;
    description = "Matched a locally-taught spam fingerprint (mail-learn-spam)";
  }
}
