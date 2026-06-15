# Catch-all SMTP sink for mail-server forwarding/relay integration tests.
# Accepts every message on :1025 and records it as JSON lines for assertions.
FROM python:3.12-slim

RUN pip install --no-cache-dir "aiosmtpd==1.4.6"

COPY sink_smtpd.py /usr/local/bin/sink_smtpd.py

EXPOSE 1025
VOLUME /var/sink
ENV SINK_MESSAGES_FILE=/var/sink/messages.json
ENTRYPOINT ["python3", "/usr/local/bin/sink_smtpd.py"]
