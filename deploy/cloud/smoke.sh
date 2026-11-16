#!/usr/bin/env bash
# Kiểm tra nhanh sau deploy: web, API, readiness, metrics có token.
set -uo pipefail
BASE="${1:?Cần URL, VD https://fieldops.duckdns.org}"
fail=0
check() { # name url expected_code [curl args...]
  local name="$1" url="$2" want="$3"; shift 3
  for i in $(seq 1 20); do
    code=$(curl -ks -o /dev/null -w '%{http_code}' "$@" "$url")
    [ "$code" = "$want" ] && { echo "✓ $name ($code)"; return 0; }
    sleep 6
  done
  echo "✗ $name: mong $want, nhận $code ($url)"; fail=1
}
check "Trang web"       "$BASE/login"            200
check "API health"      "$BASE/api/health"       200
check "API ready"       "$BASE/api/health/ready" 200
check "Auth config"     "$BASE/api/auth/config"  200
check "Metrics bị khoá" "$BASE/api/metrics"      401
if [ -n "${METRICS_TOKEN:-}" ]; then
  check "Metrics có token" "$BASE/api/metrics" 200 -H "Authorization: Bearer $METRICS_TOKEN"
fi
exit $fail
