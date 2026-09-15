#!/bin/sh
set -eu

if ! grep -Fxq 'nameserver 1.1.1.1' /etc/resolv.conf || \
   ! grep -Fxq 'nameserver 8.8.8.8' /etc/resolv.conf || \
   grep -q '127.0.0.11' /etc/resolv.conf; then
  echo 'AI resolver is not the VPN-only configuration; refusing to start' >&2
  exit 1
fi

exec uvicorn admin_ops.ai:app --host 0.0.0.0 --port "$1"
