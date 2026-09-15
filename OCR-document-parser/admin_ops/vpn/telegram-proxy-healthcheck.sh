#!/bin/sh
set -eu
# Local readiness and deny-list check, not a Telegram availability check.
code=$(curl --silent --show-error --max-time 3 --noproxy '' \
  --proxy http://127.0.0.1:8888 --output /dev/null --write-out '%{http_code}' \
  http://proxy-health.invalid/)
[ "$code" = 403 ]
