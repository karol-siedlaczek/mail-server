#!/usr/bin/env python3
"""audit-svc.py — s6 longrun stub for phase C-boot.

The full implementation (Postgres audit log writer, log-tail parser) is
provided in phase G. This stub loops forever so the s6 supervision tree stays
up and the C.9 integration test can validate the supervision graph.
"""
import time
import sys

print("[audit-svc] stub: real implementation provided in phase G", flush=True)
while True:
    time.sleep(60)
