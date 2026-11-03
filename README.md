# RustDesk Server Docker Container Image

[![Build Status](https://github.com/wodby/rustdesk/workflows/Build%20docker%20image/badge.svg)](https://github.com/wodby/rustdesk/actions)
[![Docker Pulls](https://img.shields.io/docker/pulls/wodby/rustdesk.svg)](https://hub.docker.com/r/wodby/rustdesk)

Runs the open-source [RustDesk server](https://github.com/rustdesk/rustdesk-server): the ID server (`hbbs`) and the
relay (`hbbr`) in one container, with a small web server that makes both reachable over WebSocket on one HTTP port.

The server is built from source with a patch (`patches/`): it also signs devices in over WebSocket, which the
open-source release refuses. With it, devices with the RustDesk app's **Use WebSocket** setting connect through one
HTTPS address and can be controlled as well as control others, with no other ports open.

## Docker Images

❗For better reliability we release images with stability tags (`wodby/rustdesk:1-rN`) which correspond to
[git tags](https://github.com/wodby/rustdesk/releases). We strongly recommend using images only with stability tags.

- Based on Alpine Linux, with the RustDesk server built from the official source and `patches/`
- Base image: [alpine](https://hub.docker.com/_/alpine)
- [GitHub actions builds](https://github.com/wodby/rustdesk/actions)
- [Docker Hub](https://hub.docker.com/r/wodby/rustdesk)

Supported tags:

- `1.1`, `1`, `latest`

All images are built for `linux/amd64` and `linux/arm64`.

## Run it

```bash
docker run -d --name rustdesk -v rustdesk:/data -p 8080:8080 \
  -e RUSTDESK_HOST=rustdesk.example.com \
  wodby/rustdesk:1
```

Put HTTPS for `rustdesk.example.com` in front of port 8080, then print the values to enter in every RustDesk app
under **Settings → Network**:

```bash
docker exec rustdesk rustdesk-client-settings
```

## Ports

| Port | Protocol | Used for |
| --- | --- | --- |
| 21115 | TCP | NAT type test and online status |
| 21116 | TCP and UDP | ID server: devices sign in over UDP and request connections over TCP, on the same port |
| 21117 | TCP | Relay |
| 8080 | HTTP | WebSocket routes `/ws/id` (ID server) and `/ws/relay` (relay), the status page (`/`) with setup steps, and `/healthz` |

Devices with **Use WebSocket** turned on need only port 8080 behind HTTPS. They enter the HTTPS address as the API
server, because that is how the RustDesk app chooses `wss://`; the server has no API and answers its requests with
404. Every WebSocket session goes through the relay.

Devices without WebSocket need 21115-21117, with TCP and UDP 21116 on the same public port. Set
`RUSTDESK_ID_PORT` and `RUSTDESK_RELAY_PORT` when those differ from the defaults, and `ALWAYS_USE_RELAY=Y` when
the server sees devices through a proxy.

## Key

The ID server creates a key pair in `/data` on first start (`id_ed25519` and `id_ed25519.pub`). The relay uses the
same key, so only clients set up with the public key can use the server. The container log shows it as `Key: ...`.
Keep `/data` on a volume: losing the key pair means setting up every device again.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `RUSTDESK_HOST` | | Public host name or address of the server. With `RUSTDESK_RELAY_PORT`, the ID server tells devices the relay's address |
| `RUSTDESK_ID_PORT` | | Public ID server port when it differs from 21116, for `rustdesk-client-settings` |
| `RUSTDESK_RELAY_PORT` | | Public relay port when it differs from 21117 |
| `ALWAYS_USE_RELAY` | `N` | `Y` sends every session through the relay. Use it when the server sees devices through a proxy |
| `SINGLE_BANDWIDTH` | `128` | Relay bandwidth per session, in Mbit/s |
| `TOTAL_BANDWIDTH` | `1024` | Relay bandwidth for all sessions, in Mbit/s |
| `RUST_LOG` | `info` | Log level |
| `DEBUG` | | Trace the startup scripts |

The servers read more variables, listed in RustDesk's
[configuration reference](https://github.com/rustdesk/rustdesk-server/blob/master/docs/environment-variables.md).

The container runs as user `rustdesk` (uid 1000) and exits when the ID server, the relay or the status page stops,
so the container runtime can restart it.

## Tests

`make test` runs the image built by `make` and plays two RustDesk devices through a relayed session with
`tests/probe.py`, which can also check a server elsewhere:

```bash
python3 tests/probe.py <host> <id port> <relay port> <key>
```
