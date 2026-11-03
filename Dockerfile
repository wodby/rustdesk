ARG RUST_VER=1.99.1

# The RustDesk server is built from source with patches/ applied: the open-source release signs devices in only
# over UDP, and the patch also accepts sign-ins over TCP and WebSocket, so the RustDesk app's "Use WebSocket" mode
# works through an HTTPS address.
FROM rust:${RUST_VER}-alpine AS build

ARG RUSTDESK_VER

COPY patches /patches

RUN set -eux; \
    if [ -z "${RUSTDESK_VER}" ]; then \
      echo "RUSTDESK_VER is required (example: 1.1.16)"; \
      exit 1; \
    fi; \
    apk add --no-cache build-base git perl; \
    git clone --depth 1 --branch "${RUSTDESK_VER}" --recurse-submodules --shallow-submodules \
      https://github.com/rustdesk/rustdesk-server.git /src; \
    cd /src; \
    for patch in /patches/*.patch; do git apply --verbose "${patch}"; done; \
    cargo build --release --locked --bin hbbs --bin hbbr --bin rustdesk-utils; \
    install -m 0755 target/release/hbbs target/release/hbbr target/release/rustdesk-utils /usr/local/bin/

FROM alpine:3.23

ARG RUSTDESK_VER

ENV RUSTDESK_VER="${RUSTDESK_VER}"

RUN set -eux; \
    apk upgrade --no-cache; \
    apk add --no-cache bash curl libgcc nginx tini; \
    adduser -D -u 1000 -h /data -s /bin/bash rustdesk; \
    mkdir -p /data; \
    chown rustdesk:rustdesk /data; \
    rm -f /etc/nginx/http.d/default.conf

COPY --from=build /usr/local/bin/hbbs /usr/local/bin/hbbr /usr/local/bin/rustdesk-utils /usr/local/bin/

COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.sh /
COPY bin /usr/local/bin

USER rustdesk

WORKDIR /data

VOLUME /data

# 21115: NAT test and online status, 21116: ID server (TCP and UDP), 21117: relay. 8080 serves a status page
# and the WebSocket routes /ws/id and /ws/relay, which clients with "Use WebSocket" turned on can use to connect
# to other devices.
EXPOSE 21115 21116 21116/udp 21117 8080

# tini reaps the server processes and forwards signals to them.
ENTRYPOINT ["/sbin/tini", "--", "/docker-entrypoint.sh"]

CMD ["rustdesk-server"]
