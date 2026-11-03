FROM alpine:3.23

ARG TARGETARCH

ARG RUSTDESK_VER

ENV RUSTDESK_VER="${RUSTDESK_VER}"

RUN set -eux; \
    if [ -z "${RUSTDESK_VER}" ]; then \
      echo "RUSTDESK_VER is required (example: 1.1.16)"; \
      exit 1; \
    fi; \
    apk upgrade --no-cache; \
    apk add --no-cache bash curl nginx tini; \
    case "${TARGETARCH:-amd64}" in \
      amd64) arch=amd64 ;; \
      arm64) arch=arm64v8 ;; \
      *) echo "Unsupported architecture ${TARGETARCH}"; exit 1 ;; \
    esac; \
    cd /tmp; \
    curl -fsSL --retry 3 -o rustdesk-server.zip \
      "https://github.com/rustdesk/rustdesk-server/releases/download/${RUSTDESK_VER}/rustdesk-server-linux-${arch}.zip"; \
    unzip -q rustdesk-server.zip; \
    install -m 0755 "${arch}/hbbs" "${arch}/hbbr" "${arch}/rustdesk-utils" /usr/local/bin/; \
    rm -rf /tmp/*; \
    adduser -D -u 1000 -h /data -s /bin/bash rustdesk; \
    mkdir -p /data; \
    chown rustdesk:rustdesk /data; \
    rm -f /etc/nginx/http.d/default.conf

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
