#!/bin/sh
set -eu

profile=/etc/amnezia/awg0.conf
runtime_profile=/run/awg0.conf
[ -r "$profile" ] || { echo 'AmneziaWG profile is missing or unreadable' >&2; exit 1; }
[ -e /dev/net/tun ] || { echo '/dev/net/tun is unavailable' >&2; exit 1; }

# Never execute provider-supplied wg-quick hooks or accept non-full-tunnel routes.
awk -F= '
  /^[[:space:]]*(PreUp|PostUp|PreDown|PostDown|SaveConfig|Table)[[:space:]]*=/ { bad=1 }
  /^[[:space:]]*AllowedIPs[[:space:]]*=/ && $2 ~ /0\.0\.0\.0\/0/ { full=1 }
  END { if (bad || !full) exit 1 }
' "$profile" || { echo 'Profile must contain a full IPv4 tunnel and no hooks/Table/SaveConfig' >&2; exit 1; }

endpoints=$(awk -F= '/^[[:space:]]*Endpoint[[:space:]]*=/ {
  value=$2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", value); print value
}' "$profile")
[ "$(printf '%s\n' "$endpoints" | awk 'NF { n++ } END { print n+0 }')" -eq 1 ] || {
  echo 'Exactly one VPN endpoint is required' >&2; exit 1;
}
endpoint_host=${endpoints%:*}
endpoint_port=${endpoints##*:}
printf '%s' "$endpoint_host" | grep -Eq '^[A-Za-z0-9.-]+$' || {
  echo 'Only an IPv4 address or DNS hostname is supported for the VPN endpoint' >&2; exit 1;
}
printf '%s' "$endpoint_port" | grep -Eq '^[0-9]{1,5}$' || {
  echo 'Invalid VPN endpoint port' >&2; exit 1;
}
[ "$endpoint_port" -ge 1 ] && [ "$endpoint_port" -le 65535 ] || {
  echo 'VPN endpoint port is out of range' >&2; exit 1;
}

# Only the gateway resolves the VPN endpoint before installing the firewall.
# AI workers start only after this gateway passes its healthcheck.
endpoint_ip=$(getent ahostsv4 "$endpoint_host" | awk 'NR == 1 { print $1 }')
[ -n "$endpoint_ip" ] || { echo 'VPN endpoint resolution failed' >&2; exit 1; }

umask 077
# Keep keys in a private tmpfs; the DNS field is handled by the workers' resolvers.
awk -v endpoint="$endpoint_ip:$endpoint_port" '
  /^[[:space:]]*DNS[[:space:]]*=/ { next }
  /^[[:space:]]*Endpoint[[:space:]]*=/ { print "Endpoint = " endpoint; next }
  { print }
' "$profile" > "$runtime_profile"

# Fail closed even before the tunnel is created. This ruleset is confined to
# the gateway network namespace; it does not alter the host firewall/routes.
nft add table inet awg_lock
nft 'add chain inet awg_lock block_docker_dns { type filter hook output priority -300; policy accept; }'
nft 'add chain inet awg_lock output { type filter hook output priority 0; policy drop; }'
nft 'add chain inet awg_lock input { type filter hook input priority 0; policy drop; }'
nft add rule inet awg_lock block_docker_dns ip daddr 127.0.0.11 udp dport 53 drop
nft add rule inet awg_lock block_docker_dns ip daddr 127.0.0.11 tcp dport 53 drop
nft add rule inet awg_lock output oifname lo accept
nft add rule inet awg_lock input iifname lo accept
nft add rule inet awg_lock output oifname awg0 accept
nft add rule inet awg_lock input iifname awg0 accept
nft add rule inet awg_lock output oifname eth0 ip daddr "$endpoint_ip" udp dport "$endpoint_port" accept
nft add rule inet awg_lock output oifname eth0 ct direction reply ct state established tcp sport '{ 8080, 8081, 8888 }' accept
nft add rule inet awg_lock input iifname eth0 ct state established,related accept
nft add rule inet awg_lock input iifname eth0 ct state new tcp dport '{ 8080, 8081, 8888 }' accept

awg-quick up "$runtime_profile"
echo 'AmneziaWG routing initialized'

cleanup() {
  awg-quick down "$runtime_profile" >/dev/null 2>&1 || true
  exit 0
}
trap cleanup TERM INT
while :; do
  sleep 3600 & wait "$!" || true
done
