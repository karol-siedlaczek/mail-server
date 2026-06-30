#!/usr/bin/env bash
# s6 longrun body for the in-container resolver.
#
# LOCAL_RESOLVER_ENABLED=false keeps Docker's embedded DNS (bring your own
# resolver, e.g. a private recursor via compose `dns:`). In that mode the unbound
# daemon must NOT run, but the service still has to stay "up" so the daemons that
# depend on it start — so we block in an idle sleep under s6 supervision instead
# of exiting (which would churn in a restart loop) or launching unbound.
set -euo pipefail

case "${LOCAL_RESOLVER_ENABLED:-true}" in
    0|false|FALSE|False|no|off)
        echo "[unbound] LOCAL_RESOLVER_ENABLED=false — resolver disabled; using Docker's embedded DNS"
        exec sleep infinity
        ;;
esac

# Enabled: validate then run unbound in the foreground so s6 supervises it.
unbound-checkconf || true
exec unbound -d
