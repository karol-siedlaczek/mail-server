-- Delivery event: one row per inbound LMTP delivery, correlated by queue-id.
INSERT INTO audit_logs
    (event_type, host, sender, recipient, message_id, queue_id, msg, "timestamp")
VALUES
    ('delivery', %(host)s, %(sender)s, %(recipient)s, %(message_id)s, %(queue_id)s, %(msg)s, now());
