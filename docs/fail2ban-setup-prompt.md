# Prompt for a Claude Code session: set up fail2ban for the mail-server container

Copy everything below the line into a new Claude Code session (running on the
Docker host `worker-01`, where the `mail-server` container runs).

---

## Task

Set up **fail2ban as a separate Docker container** that protects the existing
`mail-server` container (Postfix + Dovecot, image
`registry.siedlaczek.com.pl/mail-server`) by banning IPs that brute-force SMTP
submission auth or abuse port 25.

## Context you need to know

- The `mail-server` container runs Postfix (ports 25, 465, 587, 143, 993, 4190)
  and **logs to stdout** (visible via `docker logs mail-server`). It does NOT
  write a syslog file by default. The log lines are standard Postfix syslog
  format, e.g.:
  - SASL brute-force on submission:
    `postfix/submission/smtpd[...]: warning: unknown[<IP>]: SASL LOGIN authentication failed: ... sasl_username=admin@...`
  - Lost connection after AUTH:
    `postfix/submission/smtpd[...]: NOQUEUE: lost connection after AUTH from unknown[<IP>]`
  - postscreen / scanners on :25:
    `postfix/postscreen[...]: ... [<IP>]` (PREGREET, HANGUP, non-SMTP command)
- The host publishes the mail container's ports (Docker `-p`/`ports:`). Docker
  DNAT preserves the **real client source IP**, so bans on the host firewall see
  the true attacker IP.
- Crucial Docker gotcha: bans must be inserted into the **`DOCKER-USER`** iptables
  chain (or the nftables equivalent), NOT `INPUT`. Traffic to published container
  ports traverses Docker's chains and bypasses `INPUT`, so an `INPUT` DROP rule
  will not work. fail2ban's default `iptables`/`iptables-allports` action targets
  `INPUT` — you must override the chain to `DOCKER-USER`.

## Requirements

1. Run fail2ban in its own container (suggested image: `crazymax/fail2ban` — it is
   built for exactly this and handles iptables/nftables). It must run with
   `network_mode: host` and `cap_add: [NET_ADMIN, NET_RAW]` so it can manage the
   host firewall.
2. Feed it the mail server's logs. Pick the simplest reliable option and explain
   the trade-off:
   - (a) switch the `mail-server` container to the `journald` log driver and have
     fail2ban read journald, or
   - (b) have the `mail-server` container also write Postfix/Dovecot logs to a
     file on a shared volume that fail2ban mounts read-only.
   Recommend one and implement it.
3. Configure jails (in `jail.local`):
   - `postfix-sasl` — ban on repeated `SASL LOGIN authentication failed`.
   - `postfix` — generic Postfix abuse (RCPT/HELO/lost-connection floods).
   - optionally `postscreen` / `recidive` (long ban for repeat offenders).
   Sensible defaults: `maxretry=3-5`, `findtime=10m`, `bantime=1h`, escalating
   via `recidive` to e.g. 1 week.
4. Ban action must drop the IP for the affected ports using the **DOCKER-USER**
   chain. Use `action = iptables-allports` (or `nftables-allports`) with the chain
   overridden to `DOCKER-USER`, or a dedicated custom action. Verify the inserted
   rule actually appears in `iptables -L DOCKER-USER -n` (or `nft list ...`).
5. Make bans **persist across fail2ban restarts** (`bantime.increment = true`,
   persistent sqlite db on a volume).

## Deliverables

- A `docker-compose.yml` service (or `docker run` command) for fail2ban.
- `jail.local`, any custom `filter.d/*.conf` if the stock filters don't match the
  log format above, and the `action.d` override for the `DOCKER-USER` chain.
- A short README section: how logs are wired, how to test a ban
  (e.g. trigger failed AUTH and confirm the IP appears in
  `fail2ban-client status postfix-sasl` and in the `DOCKER-USER` chain), and how
  to unban.

## Verification before you finish

- `docker exec fail2ban fail2ban-client status` lists the jails.
- Deliberately fail SASL auth a few times from a test IP and confirm the IP is
  banned and the rule is present in `DOCKER-USER`.
- Confirm a legitimate IP is NOT banned and that unbanning works
  (`fail2ban-client set <jail> unbanip <IP>`).

## Out of scope

- Do not modify the `mail-server` image itself beyond (optionally) its log driver
  / an added log file mount. fail2ban is intentionally an external concern.
