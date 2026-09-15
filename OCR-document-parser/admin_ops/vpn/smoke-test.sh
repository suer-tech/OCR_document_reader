#!/usr/bin/env bash
# Runs only in CI/on a disposable Docker test host. No real VPN credentials.
set -euo pipefail

image="${1:?Gateway image tag is required}"
proxy_image="${2:?Telegram proxy image tag is required}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
scratch="$(mktemp -d /tmp/ocr-awg-smoke.XXXXXX)"
network="$(basename "$scratch")"
gateway="${network}-gateway"
worker="${network}-worker"
target="${network}-target"
telegram_target="${network}-telegram-target"
direct_proxy="${network}-direct-proxy"
vpn_proxy="${network}-vpn-proxy"
host_mark="$(sysctl -n net.ipv4.conf.all.src_valid_mark)"

cleanup() {
  result=$?
  if [ "$result" -ne 0 ]; then
    docker logs "$gateway" >&2 || true
    docker logs "$vpn_proxy" >&2 || true
    docker exec "$vpn_proxy" cat /tmp/tinyproxy.log >&2 || true
  fi
  docker rm -f "$worker" "$vpn_proxy" "$direct_proxy" "$gateway" "$target" "$telegram_target" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -f "$scratch/awg0.conf" "$scratch/hosts"
  rmdir "$scratch"
  exit "$result"
}
trap cleanup EXIT

umask 077
docker run --rm --entrypoint bash "$image" -c '
  private=$(awg genkey)
  peer=$(awg genkey | awg pubkey)
  printf "[Interface]\nPrivateKey = %s\nAddress = 10.99.0.2/32\nMTU = 1280\n[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = 127.0.0.1:51821\nPersistentKeepalive = 25\n" "$private" "$peer"
' > "$scratch/awg0.conf"
# Match the real root-owned 0600 profile with all capabilities except NET_ADMIN dropped.
sudo chown root:root "$scratch/awg0.conf"
docker network create "$network" >/dev/null
docker run -d --name "$gateway" --network "$network" \
  --cap-drop ALL --cap-add NET_ADMIN --device /dev/net/tun:/dev/net/tun \
  --sysctl net.ipv4.conf.all.src_valid_mark=1 \
  --security-opt no-new-privileges:true --read-only --init \
  --tmpfs /run:rw,nosuid,nodev,size=16m --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/awg0.conf,dst=/etc/amnezia/awg0.conf,readonly" \
  "$image" >/dev/null

ready=false
for attempt in {1..30}; do
  if docker logs "$gateway" 2>&1 | grep -Fq 'AmneziaWG routing initialized'; then ready=true; break; fi
  sleep 1
done
[ "$ready" = true ]
[ "$(docker exec "$gateway" sysctl -n net.ipv4.conf.all.src_valid_mark)" = 1 ]
docker exec "$gateway" ip -4 route get 1.1.1.1 | grep -q ' dev awg0 '
if docker logs "$gateway" 2>&1 | grep -Eq 'command not found|Read-only file system'; then
  echo 'Gateway startup contains a runtime dependency or sysctl failure' >&2
  exit 1
fi

docker run -d --name "$target" --network "$network" --cap-drop ALL \
  python:3.11-slim python -m http.server 8080 >/dev/null
docker run -d --name "$worker" --network "container:$gateway" \
  --user 10001:10001 --cap-drop ALL --read-only \
  --mount "type=bind,src=$script_dir/worker-resolv.conf,dst=/etc/resolv.conf,readonly" \
  python:3.11-slim python -m http.server 8080 >/dev/null
gateway_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$gateway")"
target_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$target")"

docker run -d --name "$telegram_target" --network "$network" --cap-drop ALL \
  python:3.11-slim python -m http.server 443 >/dev/null
telegram_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$telegram_target")"
# Synthetic DNS only for CI: no request goes to the real Telegram service.
printf '127.0.0.1 localhost\n%s api.telegram.org\n' "$telegram_ip" > "$scratch/hosts"
chmod 644 "$scratch/hosts"
for proxy in "$direct_proxy" "$vpn_proxy"; do
  proxy_network="$network"
  [ "$proxy" != "$vpn_proxy" ] || proxy_network="container:$gateway"
  docker run -d --name "$proxy" --network "$proxy_network" --cap-drop ALL \
    --read-only --init --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,nosuid,nodev,size=16m \
    --mount "type=bind,src=$scratch/hosts,dst=/etc/hosts,readonly" \
    --mount "type=bind,src=$script_dir/worker-resolv.conf,dst=/etc/resolv.conf,readonly" \
    "$proxy_image" >/dev/null
  ready=false
  for attempt in {1..15}; do
    if docker exec "$proxy" /usr/local/bin/telegram-proxy-healthcheck; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ]
done
direct_proxy_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$direct_proxy")"

test_proxy() {
  docker run --rm --network "$network" \
    --mount "type=bind,src=$script_dir/proxy-smoke.py,dst=/test.py,readonly" \
    python:3.11-slim python /test.py "$direct_proxy_ip" "$gateway_ip"
}

http_ok() {
  docker run --rm --network "$network" --entrypoint curl "$image" \
    --fail --silent --show-error --max-time 3 --retry 5 --retry-connrefused "$1" >/dev/null
}
expect_direct_blocked() {
  docker exec "$worker" python -c '
import socket, sys
try:
    connection = socket.create_connection((sys.argv[1], 8080), timeout=2)
except OSError:
    sys.exit(0)
connection.close()
sys.exit("Unexpected direct network access from AI namespace")
' "$target_ip"
}

# Positive controls prove the target and incoming admin requests work.
http_ok "http://$target_ip:8080"
http_ok "http://$gateway_ip:8080"
docker exec "$worker" python -c '
from pathlib import Path
lines = Path("/etc/resolv.conf").read_text().splitlines()
assert "nameserver 1.1.1.1" in lines and "nameserver 8.8.8.8" in lines
assert not any("127.0.0.11" in line for line in lines)
'
expect_direct_blocked
test_proxy
docker exec "$gateway" awg-quick down /run/awg0.conf
expect_direct_blocked
test_proxy
http_ok "http://$gateway_ip:8080"
[ "$(sysctl -n net.ipv4.conf.all.src_valid_mark)" = "$host_mark" ]
echo 'VPN runtime smoke passed: startup, resolver, admin replies and fail-closed egress after tunnel teardown'
