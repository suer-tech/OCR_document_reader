#!/bin/sh
set -eu

[ "$(sysctl -n net.ipv4.conf.all.src_valid_mark)" = 1 ]
ip -4 link show dev awg0 >/dev/null
nft list chain inet awg_lock output | grep -q 'policy drop'
ip -4 route get 1.1.1.1 | grep -q ' dev awg0 '
curl -4 --silent --show-error --fail --connect-timeout 5 --max-time 10 \
  https://1.1.1.1/cdn-cgi/trace | grep -q '^ip='
