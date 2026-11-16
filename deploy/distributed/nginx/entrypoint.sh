#!/bin/sh
set -eu
for domain in "$TELECOM_DOMAIN" "$EDU_DOMAIN"; do
    printf '%s' "$domain" | grep -Eq '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || exit 64
done
umask 077
envsubst '${TELECOM_DOMAIN} ${EDU_DOMAIN}' < /templates/nginx.conf > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
