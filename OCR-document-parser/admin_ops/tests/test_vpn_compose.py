"""Static guardrails for the VPN-only Codex network layout.

These checks do not replace an actual VPS tunnel/kill-switch test.
"""

from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[2]


def test_only_ai_workers_and_telegram_proxy_share_gateway_network() -> None:
    ops = yaml.safe_load((PROJECT_DIR / "docker-compose.ops.yml").read_text(encoding="utf-8"))["services"]
    fixer = yaml.safe_load((PROJECT_DIR / "docker-compose.ops-fixer.yml").read_text(encoding="utf-8"))["services"]["fixer-ai"]

    gateway = ops["awg-gateway"]
    assert gateway["cap_add"] == ["NET_ADMIN"]
    assert gateway["sysctls"]["net.ipv4.conf.all.src_valid_mark"] == "1"
    assert "/dev/net/tun:/dev/net/tun" in gateway["devices"]
    assert gateway["read_only"] is True
    assert any(volume.endswith(":/etc/amnezia/awg0.conf:ro") for volume in gateway["volumes"])

    assert "network_mode" not in ops["admin-bot"]
    assert "pulse-ai" not in ops["admin-bot"]["depends_on"]
    assert "telegram-proxy" not in ops["admin-bot"]["depends_on"]
    assert ops["admin-bot"]["environment"]["OPS_TELEGRAM_PROXY_URL"] == "http://awg-gateway:8888"
    assert ops["admin-bot"]["environment"]["OPS_PULSE_URL"] == "http://awg-gateway:8080"
    assert ops["admin-bot"]["environment"]["OPS_FIXER_URL"] == "http://awg-gateway:8081"
    assert "network_mode" not in ops["awg-gateway"]

    for worker in (ops["pulse-ai"], fixer, ops["telegram-proxy"]):
        assert worker["network_mode"] == "service:awg-gateway"
        assert worker["depends_on"]["awg-gateway"]["condition"] == "service_healthy"
        assert worker["cap_drop"] == ["ALL"]
        assert "ports" not in worker
        assert "dns" not in worker  # Docker disallows --dns with container: network mode.
        assert "./admin_ops/vpn/worker-resolv.conf:/etc/resolv.conf:ro" in worker["volumes"]
    assert ops["pulse-ai"]["command"].endswith("worker-start.sh 8080")
    assert fixer["command"].endswith("worker-start.sh 8081")
    assert "environment" not in ops["telegram-proxy"]  # No bot token or other secrets.


def test_gateway_firewall_fails_closed_on_direct_egress() -> None:
    script = (PROJECT_DIR / "admin_ops/vpn/entrypoint.sh").read_text(encoding="utf-8")
    assert "hook output priority 0; policy drop" in script
    assert "block_docker_dns" in script
    assert "ip daddr 127.0.0.11 udp dport 53 drop" in script
    assert "ip daddr 127.0.0.11 tcp dport 53 drop" in script
    assert "oifname awg0 accept" in script
    assert "oifname eth0 ip daddr \"$endpoint_ip\" udp dport \"$endpoint_port\" accept" in script
    assert "oifname eth0 ct direction reply ct state established tcp sport" in script
    assert "tcp sport '{ 8080, 8081, 8888 }'" in script


def test_telegram_proxy_restricts_destination():
    config = (PROJECT_DIR / "admin_ops/vpn/telegram-proxy.conf").read_text()
    assert "FilterDefaultDeny Yes" in config
    assert "ConnectPort 443" in config
    assert "FilterURLs No" in config
    assert (PROJECT_DIR / "admin_ops/vpn/telegram-proxy.filter").read_text().strip() == "^api[.]telegram[.]org$"
