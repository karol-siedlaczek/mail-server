# fail2ban for the mail-server container

This image does not ban anything itself. It logs Postfix SASL and Dovecot auth
failures — with the client IP — to the container log, and fail2ban on the Docker
host does the banning.

## The gotcha that makes naive setups silently useless

Traffic to a published container port is DNAT'd in `prerouting` and then
**routed**, so it traverses the `forward` hook and never reaches the host's
`input` chain. fail2ban's stock `nftables-*` and `iptables-*` actions insert
their rules into `input`. They load without error, report bans, and drop
nothing.

This is not hypothetical: on `worker-01` the pre-existing `haproxy-https` jail
sat at `Total banned: 0` while a single scanner pulled 4097 4xx responses in a
day.

Docker offers the `DOCKER-USER` chain for this, but it lives inside Docker's own
tables and is recreated on `systemctl restart docker`, discarding what was put
there. The setup below instead uses a table of its own, hooked **earlier** than
both Docker's chains and the host's own firewall:

```
table inet f2b {
    set blocked4 { type ipv4_addr; flags interval, timeout; }
    set blocked6 { type ipv6_addr; flags interval, timeout; }
    chain forward { type filter hook forward priority -10; policy accept;
                    ip saddr @blocked4 counter drop
                    ip6 saddr @blocked6 counter drop }
    chain input   { ... same two rules ... }
}
```

`drop` is terminal the instant it matches, so a banned source dies before any
`accept` further down can let it through. Covering `input` as well means one
action serves containerised and host-local jails alike. The per-element
timeouts mean bans expire on their own even if fail2ban restarts and loses
track of them.

Bans are **all-ports** by design: an IP brute-forcing SMTP AUTH has no business
reaching any other published port either. The `port =` line in each jail is
therefore cosmetic.

## Log transport

The jails read the **systemd journal**, so the container must run with Docker's
`journald` log driver:

```yaml
logging:
  driver: journald
  options:
    tag: mail-server
```

`CONTAINER_NAME` is set by the driver from the container name, independently of
`tag`, which is what `journalmatch` keys on. The driver supports no
`max-size`/`max-file`, so set the retention budget on the host
(`/etc/systemd/journald.conf`: `SystemMaxUse`) — an empty `[Journal]` section
leaves it at systemd's implicit default of 10% of the filesystem, capped at 4G.

Both daemons must actually reach that log. Their package defaults do not:
Dovecot goes to `syslog` and Rspamd to `/var/log/rspamd/rspamd.log`, both dead
ends inside a container. This image renders `conf.d/10-logging.conf`
(`log_path = /dev/stderr`, `auth_verbose = yes`) and `local.d/logging.inc`
(`type = console`) to fix that. Without `auth_verbose`, Dovecot logs no
per-attempt auth line and the `dovecot` jail has nothing to match.

## Files

None of this is configured by hand: fail2ban on the host is managed by the
`fail2ban` role in the `ansible-homelab` repo. Edit the templates there and run
`ansible-playbook site-worker.yml -t fail2ban`.

| Template in `roles/fail2ban/` | Deployed to |
| --- | --- |
| `templates/f2b-blocklist.nft.j2` | `/etc/fail2ban/f2b-blocklist.nft` |
| `templates/action.d/nftables-blocklist.conf.j2` | `/etc/fail2ban/action.d/nftables-blocklist.conf` |
| `templates/jail.d/mail.local.j2` | `/etc/fail2ban/jail.d/mail.local` |

The jails are gated on `fail2ban__mail_protection_enabled`, set per host.

The action's `actionstart` is guarded with
`nft list table inet f2b >/dev/null 2>&1 ||`, so it is a no-op for every jail
after the first and re-creates the table after a reboot without depending on
`nftables.service` (which loads only `/etc/nftables.conf`). `actionstop` is
deliberately empty, so bans survive a fail2ban restart.

`banaction = nftables-blocklist` is set once in `[DEFAULT]`, which also covers
`recidive` — it re-bans offenders originating from every jail, so it cannot use
an `input`-only action either.

## Verifying

```sh
fail2ban-client status postfix-sasl     # jail is up, counters move
nft list set inet f2b blocked4          # the ban is really in the set
journalctl CONTAINER_NAME=mail-server | grep "SASL LOGIN authentication failed"
```

Test a filter against a real line before trusting it — fail2ban **strips the
timestamp it recognises** from a line before applying `failregex`, which is a
common reason a plausible-looking regex never matches:

```sh
fail2ban-regex "<a pasted log line>" /etc/fail2ban/filter.d/dovecot.conf
```

Ban and unban by hand:

```sh
fail2ban-client set postfix-sasl banip   1.2.3.4
fail2ban-client set postfix-sasl unbanip 1.2.3.4
nft delete element inet f2b blocked4 '{ 1.2.3.4 }'   # if it outlived the jail
```

Keep the operator's own address in `ignoreip` in `jail.local` before enabling
anything.
