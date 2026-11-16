#!/bin/sh
set -eu
password=$(cat /run/secrets/redis_password)
printf '%s' "$password" | grep -Eq '^[0-9a-f]{64}$' || exit 64
umask 077
printf 'user default off\nuser app on >%s ~* &* +@all -@admin -@dangerous +info +client|setname +client|setinfo +client|id\n' "$password" > /tmp/users.acl
cat > /tmp/redis.conf <<EOF
bind 0.0.0.0
protected-mode yes
port 6379
aclfile /tmp/users.acl
dir /data
appendonly yes
appendfsync everysec
maxmemory ${REDIS_MAXMEMORY:-256mb}
maxmemory-policy noeviction
tcp-keepalive 60
logfile ""
EOF
exec redis-server /tmp/redis.conf
