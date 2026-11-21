#!/bin/sh
# keepalived health check: is this node's resolver chain answering DNS?
# Installed by lankit's dns-server role.
#
# Any reply, even NXDOMAIN or SERVFAIL, counts: the check is whether the
# process is up, not whether the internet is, so an upstream outage never
# takes the shared address off both nodes.
#
# Pi-hole (FTL) answers pi.hole itself, which says nothing about unbound
# behind it; unbound answers localhost from its built-in local zone.
dig +time=1 +tries=1 @127.0.0.1 pi.hole A >/dev/null 2>&1 || exit 1
exec dig +time=1 +tries=1 -p 5335 @127.0.0.1 localhost A >/dev/null 2>&1
