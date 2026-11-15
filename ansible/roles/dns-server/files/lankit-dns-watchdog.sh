#!/bin/sh
# lankit DNS watchdog: restart pihole-FTL when it is running but not answering.
#
# The failure this exists for (2026-07-20): FTL hung with systemd reporting it
# active and port 53 still bound, so nothing noticed and nothing restarted it.
# A crash is systemd's job (Restart=); a deliberate stop is respected. Only
# "active but silent" is acted on.
#
# Run every minute by lankit-dns-watchdog.timer. Installed by lankit's
# dns-server role; edit the role, not this file.

TAG=lankit-dns-watchdog
STAMP=${LANKIT_WATCHDOG_STAMP:-/run/lankit-dns-watchdog.last-restart}
PROBES=3          # consecutive failed probes before acting
SPACING=${LANKIT_WATCHDOG_SPACING:-5}        # seconds between probes
COOLDOWN=600      # seconds after a restart before another is allowed

systemctl is-active --quiet pihole-FTL || exit 0

# Any DNS reply, even NXDOMAIN, means FTL is serving. pi.hole is answered by
# FTL itself, so the probe doesn't depend on Unbound or the internet.
probe() { dig +time=2 +tries=1 @127.0.0.1 pi.hole A >/dev/null 2>&1; }

i=0
while [ "$i" -lt "$PROBES" ]; do
    probe && exit 0
    i=$((i + 1))
    [ "$i" -lt "$PROBES" ] && sleep "$SPACING"
done

now=$(date +%s)
if [ -f "$STAMP" ] && [ $((now - $(cat "$STAMP"))) -lt "$COOLDOWN" ]; then
    logger -t "$TAG" -p daemon.err "pihole-FTL still not answering after a recent restart; not restarting again (cooldown ${COOLDOWN}s)"
    exit 1
fi

logger -t "$TAG" -p daemon.warning "pihole-FTL active but not answering DNS ($PROBES probes, ${SPACING}s apart); restarting"
echo "$now" > "$STAMP"
systemctl restart pihole-FTL
