FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends tinyproxy-bin curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY admin_ops/vpn/telegram-proxy.conf /etc/tinyproxy/tinyproxy.conf
COPY admin_ops/vpn/telegram-proxy.filter /etc/tinyproxy/telegram.filter
COPY admin_ops/vpn/telegram-proxy-healthcheck.sh /usr/local/bin/telegram-proxy-healthcheck
RUN chmod 0755 /usr/local/bin/telegram-proxy-healthcheck
USER 10001:10001
ENTRYPOINT ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
