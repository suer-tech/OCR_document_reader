FROM golang:1.25.12-bookworm AS awg-go-build
ARG AWG_GO_COMMIT=b5928efb6ca19f0153958460c3d141f04abc5c2e
RUN git clone https://github.com/amnezia-vpn/amneziawg-go.git /src/amneziawg-go \
    && git -C /src/amneziawg-go checkout --detach "${AWG_GO_COMMIT}"
WORKDIR /src/amneziawg-go
RUN go mod download && go mod verify \
    && go build -trimpath -o /out/amneziawg-go .

FROM debian:bookworm AS awg-tools-build
ARG AWG_TOOLS_COMMIT=ee0f0a9aa34ff0a0da4b3433b9512781cfe02843
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git make gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/amnezia-vpn/amneziawg-tools.git /src/amneziawg-tools \
    && git -C /src/amneziawg-tools checkout --detach "${AWG_TOOLS_COMMIT}"
RUN make -C /src/amneziawg-tools/src wg

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl iproute2 nftables \
    && rm -rf /var/lib/apt/lists/*
COPY --from=awg-go-build /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools-build /src/amneziawg-tools/src/wg /usr/local/bin/awg
COPY --from=awg-tools-build /src/amneziawg-tools/src/wg-quick/linux.bash /usr/local/bin/awg-quick
COPY admin_ops/vpn/entrypoint.sh /usr/local/bin/awg-entrypoint
COPY admin_ops/vpn/healthcheck.sh /usr/local/bin/awg-healthcheck
RUN chmod 0755 /usr/local/bin/amneziawg-go /usr/local/bin/awg /usr/local/bin/awg-quick \
    /usr/local/bin/awg-entrypoint /usr/local/bin/awg-healthcheck
ENTRYPOINT ["/usr/local/bin/awg-entrypoint"]
