#!/bin/sh
# keepalived health check: is this node's Pi-hole answering DNS?
# Any reply, even NXDOMAIN, counts; pi.hole is answered by FTL itself.
# Installed by lankit's dns-server role.
exec dig +time=1 +tries=1 @127.0.0.1 pi.hole A >/dev/null 2>&1
