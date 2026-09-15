"""Synthetic CONNECT checks, executed in a disposable CI network, no credentials."""
import socket
import sys


def connect_status(proxy, destination, *, forward=False):
    with socket.create_connection((proxy, 8888), timeout=4) as connection:
        connection.sendall(
            f"CONNECT {destination} HTTP/1.1\r\nHost: {destination}\r\n\r\n".encode()
        )
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = connection.recv(4096)
            if not chunk:
                raise AssertionError("Proxy closed without a response")
            response += chunk
            assert len(response) < 65536
        status = int(response.split(b" ", 2)[1])
        if forward:
            assert status == 200, response
            # The test target serves plain HTTP on 443; production uses verified TLS.
            connection.sendall(b"GET / HTTP/1.0\r\nHost: api.telegram.org\r\n\r\n")
            assert b"200" in connection.recv(4096).split(b"\r\n", 1)[0]
        return status


direct_proxy, vpn_proxy = sys.argv[1:]
# Positive control: the same proxy config really allows and forwards this host.
assert connect_status(direct_proxy, "api.telegram.org:443", forward=True) == 200
for proxy in (direct_proxy, vpn_proxy):
    for destination in ("example.com:443", "api.telegram.org.evil.invalid:443", "1.1.1.1:443", "api.telegram.org:80"):
        assert connect_status(proxy, destination) == 403, destination
# Test-only hosts mapping points Telegram at a reachable bridge target. A proxy
# in the VPN namespace must never reach it through the ordinary bridge route.
try:
    status = connect_status(vpn_proxy, "api.telegram.org:443")
except (TimeoutError, ConnectionResetError):
    pass
else:
    assert status != 200, "Telegram proxy bypassed the VPN firewall"
print("Telegram proxy smoke passed: CONNECT forwarding, destination/port ACL and fail-closed egress")
