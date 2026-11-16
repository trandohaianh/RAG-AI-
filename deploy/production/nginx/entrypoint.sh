#!/bin/sh
set -eu
: "${RAG_DOMAIN:=rag.localhost}"
: "${RAG_API_DOMAIN:=api-rag.localhost}"
: "${TELECOM_DOMAIN:=telecom.localhost}"
: "${EDU_DOMAIN:=edu.localhost}"
: "${TLS_CERT_FILE:=/etc/nginx/tls/fullchain.pem}"
: "${TLS_KEY_FILE:=/etc/nginx/tls/privkey.pem}"
: "${HSTS_MAX_AGE:=0}"
for name in "$RAG_DOMAIN" "$RAG_API_DOMAIN" "$TELECOM_DOMAIN" "$EDU_DOMAIN"; do
    printf '%s' "$name" | grep -Eq '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' || exit 64
done
printf '%s' "$HSTS_MAX_AGE" | grep -Eq '^[0-9]{1,9}$' || exit 64
for path in "$TLS_CERT_FILE" "$TLS_KEY_FILE"; do
    case "$path" in /etc/nginx/tls/*|/etc/nginx/issued/*) ;; *) exit 64;; esac
    printf '%s' "$path" | grep -Eq '^/[a-zA-Z0-9_./-]+$' || exit 64
    test -r "$path" || { echo 'TLS material missing; run bootstrap first' >&2; exit 66; }
done
export RAG_DOMAIN RAG_API_DOMAIN TELECOM_DOMAIN EDU_DOMAIN TLS_CERT_FILE TLS_KEY_FILE HSTS_MAX_AGE
umask 077
envsubst '${RAG_DOMAIN} ${RAG_API_DOMAIN} ${TELECOM_DOMAIN} ${EDU_DOMAIN} ${TLS_CERT_FILE} ${TLS_KEY_FILE} ${HSTS_MAX_AGE}' < /templates/nginx.conf > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
