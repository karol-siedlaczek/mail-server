-- Send event: one row per outbound message, correlated from Postfix maillog
-- by queue-id (smtpd sasl_username+client ip, cleanup message-id, qmgr from=).
INSERT INTO audit_logs
    (event_type, login, src_ip, host, sender, recipient, message_id, queue_id, score, msg, "timestamp")
VALUES
    ('send', %(login)s, %(src_ip)s, %(host)s, %(sender)s, %(recipient)s,
     %(message_id)s, %(queue_id)s, %(score)s, %(msg)s, now());
