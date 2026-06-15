#!/usr/bin/env python3
"""Catch-all SMTP sink for mail-server integration tests.

Accepts every connection/recipient on :1025 and appends each received message
as one JSON object per line to MESSAGES_FILE so tests can assert what was
forwarded out of the mail-server. Not a real MTA: no relaying, no auth.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

from aiosmtpd.controller import Controller

MESSAGES_FILE = os.environ.get("SINK_MESSAGES_FILE", "/var/sink/messages.json")
LISTEN_HOST = os.environ.get("SINK_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("SINK_PORT", "1025"))


class SinkHandler:
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "peer": str(session.peer),
            "mail_from": envelope.mail_from,
            "rcpt_tos": list(envelope.rcpt_tos),
            "data": envelope.content.decode("utf-8", "replace"),
        }
        os.makedirs(os.path.dirname(MESSAGES_FILE), exist_ok=True)
        with open(MESSAGES_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return "250 Message accepted for delivery"


def main():
    os.makedirs(os.path.dirname(MESSAGES_FILE), exist_ok=True)
    # Truncate on boot so each test run starts clean.
    open(MESSAGES_FILE, "w", encoding="utf-8").close()
    controller = Controller(SinkHandler(), hostname=LISTEN_HOST, port=LISTEN_PORT)
    controller.start()
    print(f"sink listening on {LISTEN_HOST}:{LISTEN_PORT} -> {MESSAGES_FILE}", flush=True)
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        controller.stop()


if __name__ == "__main__":
    main()
